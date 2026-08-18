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
        # 从 JWT_SECRET 派生（开发期，生产应独立配置）
        _logger.warning("ENCRYPTION_KEY 未设置，从 JWT_SECRET 派生。生产环境应设置独立 ENCRYPTION_KEY 环境变量")
        secret = os.environ.get("JWT_SECRET", "quant-dev-secret-change-me")
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
