"""交易通道抽象基类（平台化：别人实现接口接入自己的券商/交易所）。

接口：get_credentials() / test_connection()。
实现：XTPBroker / BinanceBroker / OKXBroker（凭证从 external_interface 交易域行读，批55a 合表）。
ExecutionAdapter 已有交易接口（send_order/cancel/query），Broker 抽象"配置 + 连接测试"。
别人加 IB/CTP：实现 Broker 子类 + DB 配置。
"""
from __future__ import annotations
import json
import logging
from abc import ABC, abstractmethod

logger = logging.getLogger("broker")


class Broker(ABC):
    """交易通道接口（配置 + 连接测试；交易执行走 ExecutionAdapter）。"""

    @abstractmethod
    def get_credentials(self) -> dict:
        """返回解密后的凭证 dict（如 {app_id, app_secret} 或 {api_key, api_secret}）。"""

    @abstractmethod
    def test_connection(self) -> bool:
        """测试连接（简化：检查凭证字段完整；真连 vnpy 在服务器）。"""


class _BaseBroker(Broker):
    """通用：凭证 JSON 解密 + test 检查必需字段。"""

    REQUIRED_FIELDS: list[str] = []

    def __init__(self, credentials_encrypted: str | None = None, params: str | None = None):
        self._cred_enc = credentials_encrypted
        self._params = json.loads(params) if params else {}

    def get_credentials(self) -> dict:
        if not self._cred_enc:
            return {}
        try:
            from src.quant_common.crypto import decrypt
            raw = decrypt(self._cred_enc)
            return json.loads(raw) if raw else {}
        except Exception as e:
            logger.warning(f"解密 {self.__class__.__name__} 凭证失败: {e}")
            return {}

    def test_connection(self) -> bool:
        cred = self.get_credentials()
        return all(f in cred and cred[f] for f in self.REQUIRED_FIELDS)


class XTPBroker(_BaseBroker):
    """中泰 XTP（凭证：app_id/app_secret + 行情/交易服务器地址）。"""
    REQUIRED_FIELDS = ["app_id", "app_secret"]


class BinanceBroker(_BaseBroker):
    """币安永续（凭证：api_key/api_secret）。"""
    REQUIRED_FIELDS = ["api_key", "api_secret"]


class OKXBroker(_BaseBroker):
    """OKX 永续（凭证：api_key/api_secret/passphrase）。"""
    REQUIRED_FIELDS = ["api_key", "api_secret", "passphrase"]


class EmtBroker(_BaseBroker):
    """东财 EMT 极速柜台（批 63 P4：普通账户身份必填；信用/期权三套 FIELD_SCHEMA 可选——
    required=False 不入 REQUIRED_FIELDS，防只配普通账户的行假阴性；test=纯凭证检查同基类）。"""
    REQUIRED_FIELDS = ["emt_account", "emt_password"]


_REGISTRY: dict[str, type[Broker]] = {
    "xtp": XTPBroker,
    "emt_emq": EmtBroker,   # 批 63 P4：TD 注册表第二实例（test/切换 L1 消费面打通）
    "binance_perp": BinanceBroker,   # 26 号收尾批 C：与 create_adapter 的 *_perp 对齐
    "okx_perp": OKXBroker,
}


def get_broker(provider: str, row_id: int | None = None) -> Broker | None:
    """从 DB external_interface 交易域行实例化通道（批55a 合表）。

    row_id 指定行（M5：B 实例 HUB_INTERFACE_ROW 选账号，按 id 直取不限制能力域）；
    缺省=域内 position 序首行（勘察 #4：确定性排序防多账号选行漂移）。
    """
    cls = _REGISTRY.get(provider)
    if not cls:
        return None
    try:
        from src.data_platform.db import get_conn
        with get_conn() as conn:
            if row_id is not None:
                cur = conn.execute(
                    "SELECT credentials_encrypted, params FROM external_interface "
                    "WHERE id=%s AND provider=%s AND enabled=true", (row_id, provider))
            else:
                cur = conn.execute(
                    "SELECT credentials_encrypted, params FROM external_interface "
                    "WHERE provider=%s AND enabled=true AND 'trading' = ANY(capabilities) "
                    "ORDER BY position, id LIMIT 1", (provider,))
            r = cur.fetchone()
        if not r:
            return None
        params_str = json.dumps(r[1]) if isinstance(r[1], dict) else r[1]   # jsonb→str 喂 __init__ 契约
        return cls(credentials_encrypted=r[0], params=params_str)
    except Exception as e:
        logger.warning(f"读 external_interface({provider}) 失败: {e}")
        return None


def record_broker_usage(provider: str, action: str, symbol: str = "", success: bool = True, latency_ms: int = 0) -> None:
    """写 broker_usage 表（#37 通道用量监控）。失败不抛。"""
    try:
        from src.data_platform.db import get_conn
        with get_conn() as conn:
            conn.execute("SELECT 1 FROM broker_usage LIMIT 1")
            conn.execute("INSERT INTO broker_usage (provider,action,symbol,success,latency_ms) VALUES (%s,%s,%s,%s,%s)",
                         (provider, action, symbol, success, latency_ms))
            conn.commit()
    except Exception as e:
        logger.warning(f"broker_usage 写失败: {e}")


def runner_client_id(task_id: int | None) -> int | None:
    """worker TD 的独立 client_id（F-56，2026-09-03）。

    XTP 普通用户 client_id 须 1-99（xtp_trader_api.h:602）；hub MD 占 broker 默认号，
    多 worker TD 若共用默认号会撞号（同账户同 client_id 仅一个 TD session，后面的登录
    无法连接）。派生 2-99 区间（task_id 映射），同时 running 任务数 < 98 时不冲突。
    """
    if task_id is None:
        return None
    return (int(task_id) - 1) % 98 + 2


def xtp_setting_from(cred: dict, params: dict) -> dict:
    """cred/params → vnpy XtpGateway SETTING（中文 key）纯组装（批 63 二：hub 网关插件经此消费）。

    cred 有 app_id → 从 DB 行凭证/参数组装；无 → .env XTP_TEST_* fallback（staging/无 DB 行
    历史行为保留）。client_id 覆写由调用方处理（worker TD 派生号）。
    """
    import os
    if cred.get("app_id"):
        return {
            "账号": cred.get("app_id", ""),
            "密码": cred.get("app_secret", ""),
            "客户号": int(cred.get("client_id", params.get("client_id", 1)) or 1),
            "行情地址": params.get("md_host", ""),
            "行情端口": int(params.get("md_port", 0) or 0),
            "交易地址": params.get("td_host", ""),
            "交易端口": int(params.get("td_port", 0) or 0),
            "行情协议": "TCP",
            "授权码": cred.get("auth_code", ""),
            "日志级别": "INFO",
        }
    from dotenv import load_dotenv
    load_dotenv()
    return {
        "账号": os.environ.get("XTP_TEST_ACCOUNT", ""),
        "密码": os.environ.get("XTP_TEST_PASSWORD", ""),
        "客户号": int(os.environ.get("XTP_TEST_CLIENT_ID", "1")),
        "行情地址": os.environ.get("XTP_TEST_QUOTE_HOST", ""),
        "行情端口": int(os.environ.get("XTP_TEST_QUOTE_PORT", "0") or 0),
        "交易地址": os.environ.get("XTP_TEST_TRADE_HOST", ""),
        "交易端口": int(os.environ.get("XTP_TEST_TRADE_PORT", "0") or 0),
        "行情协议": "TCP",
        "授权码": os.environ.get("XTP_TEST_KEY", ""),
        "日志级别": "INFO",
    }


def build_xtp_setting(client_id: int | None = None, row_id: int | None = None) -> dict:
    """组装 vnpy XtpGateway SETTING（中文 key）。Broker DB 优先（PI3），.env XTP_TEST_* fallback。

    2026-08-19 从 strategy_runner.main 归位（hub/runner 双消费方；无 vnpy import——中文 key
    是普通字符串，层序不破）。批 63 二起组装收口 xtp_setting_from（本函数 = 取行 + 组装）。

    client_id 覆写（2026-08-25）：XTP 平台规则=同账号同 client_id 仅一个 MD 会话
    （官方 CreateQuoteApi 注释：多个客户端须用不同 client_id）——hub 与 direct runner
    消费同一 broker 记录必然撞号（08-22 起任务 8 "user already exists" 全部真相）。
    runner 侧传独立号（通道级配置 broker_config.params.client_id_runner）。

    row_id 指定（M5：B 实例 HUB_INTERFACE_ROW 选账号）→ 取数失败**抛异常 fail-fast（exit 78）**，
    禁 .env fallback（否则 B 静默跑在 A 的 XTP 账号上）。
    """
    import logging
    logger = logging.getLogger("strategy_framework")
    try:
        broker = get_broker("xtp", row_id)
        if broker:
            cred = broker.get_credentials()
            params = broker._params or {}
            if cred.get("app_id"):
                setting = xtp_setting_from(cred, params)
                if client_id:
                    setting["客户号"] = int(client_id)
                return setting
        if row_id is not None:
            # M5：指定 B 行但取数失败/凭证不完整 → fail-fast，禁 .env fallback
            raise RuntimeError(f"external_interface 行 id={row_id} 无 XTP 凭证或凭证不完整")
    except Exception as e:
        if row_id is not None:
            raise   # fail-fast 上抛（消费方捕获 → exit 78）
        logger.warning("Broker DB 取 XTP 凭证失败，fallback .env: %s", e)

    setting = xtp_setting_from({}, {})
    if client_id:
        setting["客户号"] = int(client_id)
    return setting


def get_interface_row(row_id: int, md_only: bool = False) -> dict:
    """读 external_interface 交易域行（批 63 二：hub 网关按行 provider 选插件）。

    返回 {provider, credentials(解密 dict), params, market, capabilities}。
    - **row_id 必填**（批 66a，D26 #6）：缺省选行分支退役——None raise ValueError
      （hub 绑死账号行 fail-fast 哲学；无主路径会静默绑首行账号）。
    - row_id 指定：直取该行；**解密失败/缺配置面 required_fields 必填字段即 raise**
      （M5 B 实例 fail-fast——禁 .env fallback 防 B 静默跑 A 账号，双盲审 P0-1/P2-1）。
      md_only=True（MD 数据面，加密 MD 网关空凭证）跳过 required_fields 校验（盲审 B P0）。
    无可用行/DB 异常 raise（消费方 exit 78）。
    """
    if row_id is None:
        raise ValueError("row_id required——缺省选行已退役（批 66a，D26 #6）：调用方须显式传接口行 id")
    try:
        from src.strategy_framework.md_gateway import list_md_gateway_providers
        gw_providers = list_md_gateway_providers()
        from src.data_platform.db import get_conn
        with get_conn() as conn:
            cur = conn.execute(
                "SELECT provider, credentials_encrypted, params, market, capabilities "
                "FROM external_interface WHERE id=%s AND enabled=true", (row_id,))
            r = cur.fetchone()
        if not r:
            raise RuntimeError(f"external_interface 无可用行（row_id={row_id}，网关 providers={gw_providers}）")
        cred = {}
        if r[1]:
            try:
                from src.quant_common.crypto import decrypt
                raw = decrypt(r[1])
                cred = json.loads(raw) if raw else {}
            except Exception as e:
                # 解密失败 fail-fast 非 .env 静默（密钥错配要炸出来——盲审 P2-1）
                raise RuntimeError(f"接口行凭证解密失败（密钥错配？）: {e}")
        params = r[2] if isinstance(r[2], dict) else (json.loads(r[2]) if r[2] else {})
        if row_id is not None and not md_only:
            # M5 fail-fast（双盲审 P0-1）：指定行按配置面 required_fields 校验——
            # 凭证非空但缺必填字段（如只有 app_secret 没 app_id）禁 .env fallback。
            # md_only=True（MD 数据面空凭证）跳过（盲审 B P0）
            from src.data_platform.interfaces import get_interface_provider
            inst = get_interface_provider(r[0])
            missing = [f for f in (inst.required_fields if inst else set()) if not cred.get(f)]
            if missing:
                raise RuntimeError(f"行 id={row_id}（{r[0]}）凭证缺必填字段 {missing}——禁 .env fallback")
        return {"provider": r[0], "credentials": cred, "params": params,
                "market": r[3], "capabilities": list(r[4] or [])}
    except Exception as e:
        logger.warning("读 external_interface 失败: %s", e)
        raise


def get_xtp_param(key: str, default=None):
    """读 external_interface xtp 交易域行 params JSON 的指定键（通道级配置正位）；缺省/异常回 default。"""
    try:
        broker = get_broker("xtp")
        if broker:
            return (broker._params or {}).get(key, default)
    except Exception:
        pass
    return default
