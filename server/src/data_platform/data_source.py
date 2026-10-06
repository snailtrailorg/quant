"""数据源抽象基类 + 注册表（平台化：别人实现 DataSource 接入自己的数据源）。

接口：get_client / test_connection / record_usage。
实现：TushareDataSource（token 从 data_source 表读，.env fallback）。
别人加 Wind：实现 DataSource 子类 + DB 配置（provider='wind'），不改 engine 代码。

⚠️ **加新源须三处各注册一类**（批 83b P1-1/P1-2：漏一处=串源，确定性 bug）：
1. 本文件 `_REGISTRY`（DataSource：连接/token/限速/熔断/用量/pacer）
2. `adapters/base.py` `_ADAPTERS`（BaseDataAdapter：fetch 契约 + capabilities 真源）
3. `interfaces/base.py` `_REGISTRY` + `_PROVIDER_MODULES`（InterfaceProvider：配置表单 schema）
漏第 1 处 → `get_data_source` 抛 `ProviderConfigError`（原为静默回落 tushare=串源）。
守门见 `tests/test_provider_registry.py`。
"""
from __future__ import annotations

import json
import logging
import os
from abc import ABC, abstractmethod

from src.quant_common.contract import ProviderConfigError

logger = logging.getLogger("data_source")


class DataSource(ABC):
    """数据源接口。

    限速（24 号抽象聚合）：`get_rate_limit(api_name)` 委托限速策略（非 abstract——
    带默认实现，AkShare stub 零改动，未来 Wind 不强制实现）。配置归
    `data_source.params` JSON：{"rate_limits": {"stk_mins": 60, ...}}。
    params 分界：秘密→credentials_encrypted；运维参数（rate_limits/base_url）→params。
    """

    DEFAULT_RATE_LIMITS: dict[str, float] = {}   # 子类覆写：api_name -> 最小间隔秒

    def __init__(self, credentials_encrypted: str | None = None, params: str | None = None,
                 interface_id: int | None = None):
        self._credentials_encrypted = credentials_encrypted
        self._params = json.loads(params) if params else {}
        self.interface_id = interface_id   # 批55a：配置行 id（record_usage 双填用；裸构造=None）
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

        默认 FixedIntervalPolicy（类默认 DEFAULT_RATE_LIMITS + DB rate_limits 覆写）。
        盲审 A-P1/B-P2：用 self.DEFAULT_RATE_LIMITS（非 {}），未来子类设类属性即生效。
        """
        from src.data_platform.rate_limit import FixedIntervalPolicy
        return FixedIntervalPolicy(self.DEFAULT_RATE_LIMITS, self._params.get("rate_limits") or {})

    def get_rate_limit(self, api_name: str) -> float:
        """该 API 两次调用最小间隔（秒）。0=不限。委托限速策略（24 号聚合）。

        键=数据源接口名（Tushare 即 pro.xxx 的 xxx，与 sync_config.tushare_api 词汇表对齐）。
        """
        return self._policy.get_interval(api_name)

    def record_usage(self, api_calls: int = 1, api_name: str = "",
                    success: bool = True, latency_ms: int = 0,
                    provider: str = "") -> None:
        """记录 API 调用到 data_source_usage 表（用量监控，A4 #36）。

        失败不抛（用量记录不影响主流程）。provider 缺省从 self.provider 取；
        interface_id（批55a 键升级）从实例注入取——get_data_source 读行时带上，
        裸构造（测试/.env fallback）为 NULL，provider 列仍双填保聚合视图不破。
        """
        try:
            from src.data_platform.db import get_conn
            prov = provider or getattr(self, "provider", "unknown")
            with get_conn() as conn:
                conn.execute(
                    "INSERT INTO data_source_usage (provider, api_name, calls, success, latency_ms, interface_id) "
                    "VALUES (%s,%s,%s,%s,%s,%s)",
                    (prov, api_name, api_calls, success, latency_ms, self.interface_id))
                conn.commit()
        except Exception:  # 失败不阻断（fail-open 降级）  # noqa: S110
            pass


class TushareDataSource(DataSource):
    """Tushare 数据源（token 从 data_source 表读，.env fallback）。"""

    provider = "tushare"

    # 类级默认限速（T 审：= engine 今日硬编码值，DB 无 rate_limits 时行为不变）
    DEFAULT_RATE_LIMITS = {
        "stk_mins": 3600.0,     # 分钟线 per-symbol（实测 1 次/小时，2026-08-19）
        "adj_factor": 0.3,       # 复权因子回补
        "daily": 0.5,            # 日线按交易日
        "daily_basic": 0.5,      # 基本面指标（2026-08-27 补：engine 收编 sleep 后走此档）
        "fund_daily": 0.5,
        "cb_daily": 0.5,
        "index_daily": 0.5,    # 批 73：收编补档（DEFAULT_RATE_LIMITS 原无键→收编后仍不限速的缺口）
        "trade_cal": 0.5,
        "stock_basic": 0.5,
        # 批 64b：漏网限速档补齐（D25 收尾——同步链两循环全覆盖，全 0.3s 与 adj_factor
        # 同档=200 积分贴线保守值；DB rate_limits 两级覆写不变）
        # pool_data per-symbol 族（0106 登记十表）：
        "income": 0.3, "balancesheet": 0.3, "cashflow": 0.3, "fina_indicator": 0.3,
        "cyq_chips": 0.3, "top10_holders": 0.3, "dividend": 0.3, "pledge_stat": 0.3,
        "share_float": 0.3, "stk_holdernumber": 0.3,
        # engine 侧（tier1 七键从 daily 0.5s 归位 per-API + 四裸调点首接限速）：
        "stk_limit": 0.3, "moneyflow": 0.3, "margin_detail": 0.3, "top_list": 0.3,
        "block_trade": 0.3, "cyq_perf": 0.3, "forecast": 0.3,
        "cb_basic": 0.3, "fund_basic": 0.3, "namechange": 0.3, "concept": 0.3,
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


class BinanceDataSource(DataSource):
    """币安公开数据源（批 101）——**0 密钥**、只读批量历史（官方 `data.binance.vision`）。

    与 `binance_perp`（交易通道：api_key/api_secret 下单/实时）**是两把键**：
    `binance` = 拉数面（本类，公开端点本就无鉴权，故无凭证字段）；
    `binance_perp` = 下单面（NON_DATA_PROVIDERS，批 101 之前的既有键）。

    限速：批量站是**静态文件 CDN**（无 weight 模型、无配额概念）⇒ `DEFAULT_RATE_LIMITS` 空、
    不设限速；礼貌性由 adapter 的 ≤8 并发表达。**刻意不接 `rate_limit_context`**——
    `_get_rate_ds("binance")` 若回落 tushare 兜底源会把币安的下载计进 tushare 的熔断器
    （串源），故 `engine._sync_binance_perp_daily` 直接走 adapter，不取限速句柄。

    `test_connection` 是可达性探测（GET 批量站已知文件）——这是本源的**真 gate**：
    2026-10-06 prod 实测 `data.binance.vision` 直连 200，而 `fapi.binance.com`（实时）被阻断。
    """

    provider = "binance"
    DEFAULT_RATE_LIMITS: dict[str, float] = {}   # 静态文件 CDN：无限速档

    def get_client(self):
        """本源无「客户端对象」——数据取用是 HTTP GET 批量 ZIP（在 adapter 内完成），无 SDK 句柄。

        显式返回 None（而非编造一个假对象）：调用方若需要「拉数」应走 `adapter.fetch_supply`，
        不要指望本方法给出可用句柄——`_get_pro` 那条 tushare-only 的路径与本源无关。
        """
        return None

    def test_connection(self) -> bool:
        """可达性探测：GET 批量站的已知小文件（BTCUSDT 首个完整月包）。200 即可达。"""
        import urllib.request
        url = ("https://data.binance.vision/data/futures/um/monthly/klines/"
               "BTCUSDT/1d/BTCUSDT-1d-2020-01.zip")
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "quant-data-sync/1.0"})
            with urllib.request.urlopen(req, timeout=10):
                return True
        except Exception as e:
            logger.warning(f"Binance 连接测试失败: {e}")
            return False


# ── 批 103b：聚宽数据源（账号/密码，额度制） ──

class JoinQuantDataSource(DataSource):
    """聚宽 JQData 数据源（批 103b 真接）——`jqdatasdk` 账号/密码，**额度制**（行计数）。

    真源事实（2026-10-06 dev 实测，勿凭文档）：
    - **auth 门**：jqdatasdk **所有**调用（含纯本地的 `normalize_code`）前置 `auth`，
      未登录抛 "Please run jqdatasdk.auth first" ⇒ 本类 `get_client()` 返回**已 auth** 的模块句柄。
    - **额度按「返回行数」计**（`get_query_count() -> {"total":1e6,"spare":…}`）——**不是调用次数**。
      故 `DEFAULT_RATE_LIMITS` 留空（间隔制模型对本源无意义），行预算由调用方（engine handler）管。
    - **窗口是绝对区间**（`get_account_info().date_range_start/end`），**非**「今日−15月~今日−3月」滚动式。
    - **连接数 = 1**：同刻只允许一个任务 auth ⇒ 调用方须持 **provider 级**互斥锁
      （`SyncLock("provider:joinquant")`），per-sync_id 锁不够。
    - **SDK 边界检查不对称**：只查 `end_date ∈ [窗口起, 窗口止]`，**`start_date` 完全不查**
      （实测 `2020-01-01~2026-01-01` 放行返 1455 行）⇒ 调用方**必须自夹 start**，
      否则单次请求可静默拉回全史 = 额度炸弹。
    """

    provider = "joinquant"
    DEFAULT_RATE_LIMITS: dict[str, float] = {}   # 额度制（行计数），非间隔制

    def __init__(self, credentials_encrypted: str | None = None, params: str | None = None,
                 interface_id: int | None = None):
        super().__init__(credentials_encrypted, params, interface_id)
        self._jq = None   # auth 后的 jqdatasdk 模块句柄（实例内缓存；跨实例不共享）

    def _get_credentials(self) -> tuple[str, str]:
        """解密 (account, password)。DB 优先，.env `JQDATA_USER`/`JQDATA_PASSWORD` 兜底。

        凭证形态：`credentials_encrypted` 解密后是 **JSON 对象**（键＝`interfaces/joinquant.py`
        的 `FIELD_SCHEMA` 键：account/password）——写侧 `mgmt._validate_credentials` 即按此约定校验。
        容错：非 JSON 时按 `account:password` 单串解析（手工放库的运维路径）。
        """
        raw = ""
        if self._credentials_encrypted:
            try:
                from src.quant_common.crypto import decrypt
                raw = decrypt(self._credentials_encrypted)
            except Exception as e:
                logger.warning(f"解密聚宽凭证失败: {e}")
        if raw:
            try:
                d = json.loads(raw)
                if isinstance(d, dict):
                    return str(d.get("account") or d.get("user") or ""), str(d.get("password") or "")
            except (TypeError, ValueError):
                if ":" in raw:
                    a, b = raw.split(":", 1)
                    return a, b
                return raw, ""
        return os.environ.get("JQDATA_USER", ""), os.environ.get("JQDATA_PASSWORD", "")

    def get_client(self):
        """返回**已 auth** 的 jqdatasdk 模块（auth 门见类 docstring）。

        凭证缺失 → 抛 `ProviderConfigError`（响亮失败，不静默回落 tushare——回落会把聚宽的
        额度/用量记到 tushare 头上，即串源）。
        """
        import jqdatasdk as jq
        if self._jq is None:
            account, password = self._get_credentials()
            if not account or not password:
                raise ProviderConfigError(
                    "聚宽凭证缺失：请在数据源页为 joinquant 填写 account/password，"
                    "或设 .env JQDATA_USER/JQDATA_PASSWORD")
            jq.auth(account, password)
            self._jq = jq
        return self._jq

    def account_window(self) -> dict:
        """账号可用窗口 + 额度（`get_account_info()` / `get_query_count()` 真值）。

        返回 {start, end, expire_time, spare, total}；`start/end` 为 `datetime.date`。
        窗口**动态取**（禁硬编码）——续期/到期会变。
        """
        import datetime as _dt
        jq = self.get_client()
        info = jq.get_account_info() or {}
        qc = jq.get_query_count() or {}
        return {
            "start": _dt.datetime.strptime(str(info.get("date_range_start"))[:10], "%Y-%m-%d").date(),
            "end": _dt.datetime.strptime(str(info.get("date_range_end"))[:10], "%Y-%m-%d").date(),
            "expire_time": str(info.get("expire_time") or ""),
            "spare": int(qc.get("spare") or 0),
            "total": int(qc.get("total") or 0),
        }

    def test_connection(self) -> bool:
        """auth + 额度查询（最小真实调用，不耗行数——get_query_count 是元数据）。"""
        try:
            return self.account_window()["total"] > 0
        except Exception as e:
            logger.warning(f"聚宽连接测试失败: {e}")
            return False


# ── 批 102b：OKX 数据源（0 密钥，公共行情；**prod 必须经代理出口**） ──

class OkxDataSource(DataSource):
    """OKX 公开数据源（批 102b）——**0 密钥**、只读公共行情（`www.okx.com/api/v5/market/*`）。

    与 `okx_perp`（交易通道：api_key/api_secret/passphrase 下单/实时）**是两把键**：
    `okx` = 拉数面（本类，公共端点本就无鉴权，故无凭证字段）；
    `okx_perp` = 下单面（NON_DATA_PROVIDERS，批 63 既有桩）。混用会让 `_get_supply_adapter`
    找不到 adapter 而静默回落 tushare 拉错源（与 binance / binance_perp 同族）。

    🔴 **本源的真 gate 是「出口」不是「凭证」**（2026-10-06 实测）：prod 上 `www.okx.com`
    被解析到 `169.254.0.2`（污染）⇒ 直连不可达；只有经代理出口（102a 的
    `proxy_binding.consumer='okx'`）才通。故 `test_connection` **复用同一出口配置**做探测
    ——若像 `binance` 那样裸直连探测，prod 上会把「配好代理即可用」误报成「不可用」，
    把运维引向错误方向。

    限速：IP 级 **20 req / 2s**（代理出口 IP 共享）——由 adapter 内滚动窗口
    （`OkxAdapter._wait_slot`）表达，**不用 `DEFAULT_RATE_LIMITS` 间隔制**；
    **刻意不接 `rate_limit_context`**（同 `BinanceDataSource`：`_get_rate_ds('okx')` 若回落
    tushare 兜底源会把 OKX 的请求计进 tushare 的熔断器＝串源）。
    """

    provider = "okx"
    DEFAULT_RATE_LIMITS: dict[str, float] = {}   # 窗口制在 adapter 内表达，非间隔制

    def get_client(self):
        """本源无 SDK 句柄——取数是 HTTP GET JSON（在 adapter 内完成）。

        显式返回 None（而非编造假对象）：调用方要拉数应走 `adapter.fetch_supply`。
        """
        return None

    def test_connection(self) -> bool:
        """可达性探测：GET `public/instruments?instType=SWAP`，**经该消费方的出口配置**。

        判据＝`code == '0'`（OKX 业务码；HTTP 200 也可能是错误体）。出口解析失败不抛——
        回落直连再探（失败本身即诊断结果，端点须如实回 False 而非 500）。
        """
        import requests

        from src.data_platform.proxy import proxies_map, resolve_proxy
        try:
            proxy = resolve_proxy(self.provider)
        except Exception as e:                     # 表缺/DB 抖动 → 裸连探测（失败=结果）
            logger.warning(f"OKX 出口配置解析失败，回落直连探测: {e}")
            proxy = None
        url = "https://www.okx.com/api/v5/public/instruments?instType=SWAP"
        try:
            resp = requests.get(url, timeout=10, proxies=proxies_map(proxy),
                                headers={"User-Agent": "quant-data-sync/1.0"})
            resp.raise_for_status()
            return str(resp.json().get("code")) == "0"
        except Exception as e:
            logger.warning(f"OKX 连接测试失败: {e}")
            return False


# ── 注册表：provider -> DataSource 类（别人加数据源在此注册） ──

_REGISTRY: dict[str, type[DataSource]] = {
    "tushare": TushareDataSource,
    "binance": BinanceDataSource,   # 批 101：加密永续日线（0 密钥批量历史）
    "joinquant": JoinQuantDataSource,  # 批 103b：聚宽 A 股历史切片（账号/密码，额度制）
    "okx": OkxDataSource,   # 批 102b：OKX 永续日线（0 密钥公共行情，经代理出口）
}

# 兜底源声明（**单点真相**）：所有源都无实例可用时的回落目标。
# 为什么需要它：盲审 A-P2/B-P2 裁定"配置错 provider 不应打断同步"（fail-soft），
# 故无实例时须回落一个可用源；而这个回落目标若不在此声明、任由散落的"provider 是否
# 等于某个源名"字面量分支散在各层，就正好撞上 CI 断言一（test_contract_gate：全仓禁
# provider 字面量分支，多源路由必须查表）。声明在此处后，engine 只问"是否等于声明的
# 兜底源"，新增数据源无需改 engine 一行。
FALLBACK_PROVIDER: str = "tushare"


def fallback_data_source() -> DataSource:
    """无可用实例时的兜底 DataSource（A-P2/B-P2 fail-soft 的**唯一出口**）。

    语义=tushare 专属的".env token 直连"路径（DB 无配置行时仍可拉数）。
    """
    return _REGISTRY[FALLBACK_PROVIDER]()


def get_data_source(provider: str) -> DataSource | None:
    """从 DB data_source 表读数据源配置行实例化对应 DataSource（批 83a 拆表）。

    **三分域语义（批 83b P0 防串源）**：
    - provider 在 adapter 注册表**已注册**但本表 `_REGISTRY` 未注册 → **抛
      `ProviderConfigError`**（fail-fast，EX_CONFIG 语义）。这是**串源必经之路**：调用方
      普遍写 `get_data_source(p) or TushareDataSource()`，静默回落会让新源的失败/限速/
      熔断/用量全记到 tushare 头上（确定性 bug），故必须响亮失败而非降级。
    - provider 在本表已注册、DB 无匹配行 → 返回 None（调用方按需要走 .env fallback——
      tushare 合法的"未配库行"路径）。
    - provider **两边都未注册**（完全未知）→ 返回 None，由 engine 层维持盲审 A-P2/B-P2
      的 fail-soft + 告警（"配置错 provider 不应打断同步"）。两裁定各守其域。

    选行=enabled 过滤+表内 position 序（勘察 #3：确定性排序防多账号选行漂移）。
    83a 拆表后表内行恒为数据源（原 `NOT ('trading' = ANY(capabilities))` 域谓词在表层面退役）。
    """
    cls = _REGISTRY.get(provider)
    if not cls:
        # 懒 import 断开 data_source ↔ adapters 的模块级回环（adapters 反向依赖本模块）
        from src.data_platform.adapters.base import _ADAPTERS
        if provider in _ADAPTERS:
            raise ProviderConfigError(
                f"provider={provider} 已注册 adapter 但未注册 DataSource——加新源须"
                f"**三处各注册一类**（data_source._REGISTRY / adapters._ADAPTERS / "
                f"interfaces._REGISTRY），否则熔断/限速/用量会串到 tushare 头上。"
                f"当前已注册 DataSource={sorted(_REGISTRY)}；若该源暂时不完整，"
                f"请勿在 sync_config 里选它")
        return None
    try:
        from src.data_platform.db import get_conn
        with get_conn() as conn:
            cur = conn.execute(
                "SELECT id, credentials_encrypted, params FROM data_source "
                "WHERE provider=%s AND enabled=true "
                "ORDER BY position, id LIMIT 1", (provider,))
            r = cur.fetchone()
        if not r:
            return None
        params_str = json.dumps(r[2]) if isinstance(r[2], dict) else r[2]   # jsonb→str 喂 __init__ 契约
        return cls(credentials_encrypted=r[1], params=params_str, interface_id=r[0])
    except ProviderConfigError:
        raise                      # 不吞（形态错是配置错，不是"读表失败"）
    except Exception as e:
        logger.warning(f"读 data_source({provider}) 失败: {e}")
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
            import akshare  # noqa: F401  # import 成功即可用（可用性探测）
            return True
        except ImportError:
            return False
    def record_usage(self, api_calls=1, api_name="", success=True, latency_ms=0, provider=""):
        pass  # AkShare 免费无配额
