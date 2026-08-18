"""交易通道抽象基类（平台化：别人实现接口接入自己的券商/交易所）。

接口：get_credentials() / test_connection()。
实现：XTPBroker / BinanceBroker / OKXBroker（凭证从 broker_config DB 读）。
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


_REGISTRY: dict[str, type[Broker]] = {
    "xtp": XTPBroker,
    "binance": BinanceBroker,
    "okx": OKXBroker,
}


def get_broker(provider: str) -> Broker | None:
    """从 DB broker_config 实例化通道。"""
    cls = _REGISTRY.get(provider)
    if not cls:
        return None
    try:
        from src.data_platform.db import get_conn
        with get_conn() as conn:
            cur = conn.execute(
                "SELECT credentials_encrypted, params FROM broker_config "
                "WHERE provider=%s AND enabled=true LIMIT 1", (provider,))
            r = cur.fetchone()
        if not r:
            return None
        return cls(credentials_encrypted=r[0], params=r[1])
    except Exception as e:
        logger.warning(f"读 broker_config({provider}) 失败: {e}")
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


def build_xtp_setting() -> dict:
    """组装 vnpy XtpGateway SETTING（中文 key）。Broker DB 优先（PI3），.env XTP_TEST_* fallback。

    2026-08-19 从 strategy_runner.main 归位（hub/runner 双消费方；无 vnpy import——中文 key
    是普通字符串，层序不破）。
    """
    import logging
    import os
    logger = logging.getLogger("strategy_framework")
    try:
        broker = get_broker("xtp")
        if broker:
            cred = broker.get_credentials()
            params = broker._params or {}
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
    except Exception as e:
        logger.warning("Broker DB 取 XTP 凭证失败，fallback .env: %s", e)

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
