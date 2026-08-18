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
    """数据源接口。"""

    @abstractmethod
    def get_client(self):
        """返回数据源客户端（如 tushare pro 对象、Wind 客户端）。"""

    @abstractmethod
    def test_connection(self) -> bool:
        """测试连接，返回是否成功。"""

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

    def __init__(self, credentials_encrypted: str | None = None, params: str | None = None):
        self._credentials_encrypted = credentials_encrypted
        self._params = json.loads(params) if params else {}

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
