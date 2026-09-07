"""数据源抽象基类 + 注册表（平台化：别人实现 DataSource 接入自己的数据源）。

接口：get_client / test_connection / record_usage。
实现：TushareDataSource（token 从 data_source_config DB 读，.env fallback）。
别人加 Wind：实现 DataSource 子类 + DB 配置（provider='wind'），不改 engine 代码。
"""
from __future__ import annotations
import os
import json
import logging
from abc import ABC, abstractmethod

logger = logging.getLogger("data_source")


class DataSource(ABC):
    """数据源接口。

    限速（2026-08-19 T 审）：`get_rate_limit(api_name)` 具体方法（非 abstract——
    带默认实现，AkShare stub 零改动，未来 Wind 不强制实现）。配置归
    `data_source_config.params` JSON：{"rate_limits": {"stk_mins": 60, ...},
    "rate_time_overrides": [{"window":"16:00-20:00","multiplier":2.5}]}。
    params 分界：秘密→credentials_encrypted；运维参数（rate_limits/rate_time_overrides/
    base_url）→params。
    """

    DEFAULT_RATE_LIMITS: dict[str, float] = {}   # 子类覆写：api_name -> 最小间隔秒

    def __init__(self, credentials_encrypted: str | None = None, params: str | None = None):
        self._credentials_encrypted = credentials_encrypted
        self._params = json.loads(params) if params else {}
        self._policy = self._build_rate_policy()

    def get_param(self, *keys, default=None):
        """按命名空间路径读 params——get_param("circuit_breaker", "fail_threshold")。

        通用参数读取器（B 层）：路径任一层不存在 / 中途非 dict → 回 default，
        不抛异常（运维参数非必填，非法配置不崩同步）。
        """
        v = self._params
        for k in keys:
            if not isinstance(v, dict):
                return default
            v = v.get(k)
        return v if v is not None else default

    def get_param_float(self, *keys, default: float, lo: float, hi: float) -> float:
        """读 float + 范围钳位（防呆护栏在后端，不信任前端/DB 里的手写值）。

        非法（None/非数字字符串）回落 default + 告警；越界钳到 [lo, hi]。
        """
        v = self.get_param(*keys, default=default)
        try:
            return max(lo, min(hi, float(v)))
        except (TypeError, ValueError):
            logger.warning("params.%s=%r 非法，回落 %s", ".".join(keys), v, default)
            return default

    @abstractmethod
    def get_client(self):
        """返回数据源客户端（如 tushare pro 对象、Wind 客户端）。"""

    @abstractmethod
    def test_connection(self) -> bool:
        """测试连接，返回是否成功。"""

    def _build_rate_policy(self):
        """子类覆写：构建限速策略（24 号限速抽象聚合，各平台自己实现，高内聚低耦合）。

        默认 FixedIntervalPolicy（类默认 {} + DB rate_limits 覆写）。
        """
        from src.data_platform.rate_limit import FixedIntervalPolicy
        return FixedIntervalPolicy({}, self._params.get("rate_limits") or {})

    def get_rate_limit(self, api_name: str) -> float:
        """该 API 两次调用最小间隔（秒）。0=不限。委托限速策略（24 号聚合）。

        键=数据源接口名（Tushare 即 pro.xxx 的 xxx，与 sync_config.tushare_api 词汇表对齐）。
        """
        return self._policy.get_interval(api_name)

    def record_usage(self, api_calls: int = 1, api_name: str = "",
                    success: bool = True, latency_ms: int = 0,
                    provider: str = "") -> None:
        """记录 API 调用到 data_source_usage 表（用量监控，A4 #36）。

        失败不抛（用量记录不影响主流程）。provider 缺省从 self.provider 取。
        """
        try:
            from src.data_platform.db import get_conn
            prov = provider or getattr(self, "provider", "unknown")
            with get_conn() as conn:
                conn.execute(
                    "INSERT INTO data_source_usage (provider, api_name, calls, success, latency_ms) "
                    "VALUES (%s,%s,%s,%s,%s)",
                    (prov, api_name, api_calls, success, latency_ms))
                conn.commit()
        except Exception:
            pass


class TushareDataSource(DataSource):
    """Tushare 数据源（token 从 data_source_config DB 读，.env fallback）。"""

    provider = "tushare"

    # 类级默认限速（T 审：= engine 今日硬编码值，DB 无 rate_limits 时行为不变）
    DEFAULT_RATE_LIMITS = {
        "stk_mins": 3600.0,     # 分钟线 per-symbol（实测 1 次/小时，2026-08-19）
        "adj_factor": 0.3,       # 复权因子回补
        "daily": 0.5,            # 日线按交易日
        "daily_basic": 0.5,      # 基本面指标（2026-08-27 补：engine 收编 sleep 后走此档）
        "fund_daily": 0.5,
        "cb_daily": 0.5,
        "trade_cal": 0.5,
        "stock_basic": 0.5,
    }

    def _build_rate_policy(self):
        """Tushare 限速策略：FixedIntervalPolicy（类默认 DEFAULT_RATE_LIMITS + DB rate_limits 覆写）。

        24 号限速抽象聚合：去掉积分档（points_tier/POINTS_PRESETS）+ 时段乘数
        （rate_time_overrides），限速值由「类默认 + DB 覆写」两级决定。
        """
        from src.data_platform.rate_limit import FixedIntervalPolicy
        return FixedIntervalPolicy(self.DEFAULT_RATE_LIMITS, self._params.get("rate_limits") or {})

    def _get_token(self) -> str:
        """解密 token（DB 优先，.env fallback）。"""
        if self._credentials_encrypted:
            try:
                from src.quant_common.crypto import decrypt
                return decrypt(self._credentials_encrypted)
            except Exception as e:
                logger.warning(f"解密 Tushare token 失败: {e}")
        return os.environ.get("TUSHARE_TOKEN", "")

    def get_client(self):
        import tushare as ts
        return ts.pro_api(self._get_token())

    def test_connection(self) -> bool:
        try:
            pro = self.get_client()
            df = pro.trade_cal(exchange="SSE", limit=1)
            return df is not None and not df.empty
        except Exception as e:
            logger.warning(f"Tushare 连接测试失败: {e}")
            return False


# ── 注册表：provider -> DataSource 类（别人加数据源在此注册） ──

_REGISTRY: dict[str, type[DataSource]] = {
    "tushare": TushareDataSource,
}


def get_data_source(provider: str) -> DataSource | None:
    """从 DB 读 data_source_config 实例化对应 DataSource。

    provider 不存在或无配置返回 None（调用方 fallback .env）。
    """
    cls = _REGISTRY.get(provider)
    if not cls:
        return None
    try:
        from src.data_platform.db import get_conn
        with get_conn() as conn:
            cur = conn.execute(
                "SELECT credentials_encrypted, params FROM data_source_config "
                "WHERE provider=%s AND enabled=true LIMIT 1", (provider,))
            r = cur.fetchone()
        if not r:
            return None
        return cls(credentials_encrypted=r[0], params=r[1])
    except Exception as e:
        logger.warning(f"读 data_source_config({provider}) 失败: {e}")
        return None


class AkShareDataSource(DataSource):
    """AkShare 数据源（P3-17 stub，免费无 token，补充 Tushare 不足）。

    AkShare 无需 API key，直接 akshare 库拉数据。
    暂未注册到 _REGISTRY（需安装 akshare 库 + 实现具体接口）。
    """
    def get_client(self):
        import akshare as ak
        return ak
    def test_connection(self) -> bool:
        try:
            import akshare
            return True
        except ImportError:
            return False
    def record_usage(self, api_calls=1, api_name="", success=True, latency_ms=0, provider=""):
        pass  # AkShare 免费无配额
