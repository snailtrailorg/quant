"""密钥 AES 加密工具 -- S-MKT-003。

API key 加密入库，前端脱敏，仅支持重新录入。
"""

from __future__ import annotations
from src.data_platform.db import get_conn
import os
import base64
import hashlib
import threading
import logging
from cryptography.fernet import Fernet
from dotenv import load_dotenv

load_dotenv()

_logger = logging.getLogger("quant")

# 从环境变量获取加密密钥（首次启动自动生成）
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


def store_api_key(name: str, exchange: str, api_key: str, api_secret: str = "") -> None:
    """加密存储 API key 到 accounts 表。"""
    import psycopg
    enc_key = encrypt(api_key)
    enc_secret = encrypt(api_secret) if api_secret else ""
    hint = mask(api_key)
    with get_conn() as conn:
        conn.execute("SELECT 1 FROM accounts LIMIT 1")
        conn.execute("""
            INSERT INTO accounts (name, exchange, api_key_enc, api_secret_enc, api_key_hint)
            VALUES (%s,%s,%s,%s,%s)
            ON CONFLICT (id) DO UPDATE SET api_key_enc=EXCLUDED.api_key_enc, api_secret_enc=EXCLUDED.api_secret_enc, api_key_hint=EXCLUDED.api_key_hint
        """, (name, exchange, enc_key, enc_secret, hint))
        conn.commit()


def get_api_key(account_id: int) -> tuple[str, str]:
    """解密读取 API key（仅后端使用，前端不可见）。"""
    import psycopg
    with get_conn() as conn:
        cur = conn.execute("SELECT api_key_enc, api_secret_enc FROM accounts WHERE id=%s", (account_id,))
        row = cur.fetchone()
        if not row:
            return "", ""
        return decrypt(row[0]) if row[0] else "", decrypt(row[1]) if row[1] else ""
