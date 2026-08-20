"""加密工具（从 web_api/crypto_utils.py 归位，2026-08-19 P 审）。

死代码 store_api_key/get_api_key（DB 耦合且全仓零调用）随迁删除——记录于 decisions.md。
"""
from __future__ import annotations
import os
import base64
import hashlib
import threading
import logging
from cryptography.fernet import Fernet
from dotenv import load_dotenv

load_dotenv()

_logger = logging.getLogger("quant")


def _get_encryption_key() -> bytes:
    key = os.environ.get("ENCRYPTION_KEY", "")
    if not key:
        secret = os.environ.get("JWT_SECRET", "")
        if not secret:
            # P4 修复（2026-08-20 审计 B-质量面④）：原回落公开常量"quant-dev-secret-change-me"——
            # 读过源码的人即可解开全部"加密"凭证。改为进程内随机密钥（重启即孤儿化已加密数据，
            # 比"众人皆知"安全；响亮告警提示配 ENCRYPTION_KEY。实盘模式由 SD1 拒启动兜底）
            import secrets as _secrets
            _logger.critical("JWT_SECRET 与 ENCRYPTION_KEY 均未设置——使用进程内随机密钥，"
                             "重启后已加密凭证将无法解密！立即配置（详见待办 #47）")
            secret = _secrets.token_urlsafe(48)
        _logger.warning("ENCRYPTION_KEY 未设置，从 JWT_SECRET 派生。生产环境应设置独立 ENCRYPTION_KEY 环境变量")
        key = base64.urlsafe_b64encode(hashlib.sha256(secret.encode()).digest()).decode()
    return key.encode()


_fernet = None
_fernet_lock = threading.Lock()


def _get_fernet() -> Fernet:
    global _fernet
    with _fernet_lock:
        if _fernet is None:
            _fernet = Fernet(_get_encryption_key())
    return _fernet


def encrypt(plaintext: str) -> str:
    """加密明文 -> 返回密文(base64)。"""
    return _get_fernet().encrypt(plaintext.encode()).decode()


def decrypt(ciphertext: str) -> str:
    """解密密文 -> 返回明文。"""
    return _get_fernet().decrypt(ciphertext.encode()).decode()


def mask(key: str, visible: int = 4) -> str:
    """密钥脱敏：前 visible 位 + ***。"""
    if len(key) <= visible:
        return "***"
    return key[:visible] + "***" + key[-2:]
