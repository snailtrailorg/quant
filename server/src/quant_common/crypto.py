"""加密工具（从 web_api/crypto_utils.py 归位，2026-08-19 P 审）。

密钥派生（2026-08-22 SECRET_KEY 根密钥方案）：
  优先级链：SECRET_KEY → HKDF("encrypt") → ENCRYPTION_KEY（推荐）
            ENCRYPTION_KEY 环境变量（向后兼容）
            JWT_SECRET → sha256（向后兼容，有告警）
            均未设 → 进程内随机密钥（重启孤儿化，critical 告警）
"""
from __future__ import annotations
import os
import base64
import hashlib
import threading
import logging
from cryptography.fernet import Fernet
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from cryptography.hazmat.primitives import hashes
from dotenv import load_dotenv

load_dotenv()

_logger = logging.getLogger("quant")


def _derive_key(root: str, context: bytes, length: int = 32) -> str:
    """HKDF 派生子密钥（确定性，同一输入始终输出相同结果）。
    用于从 SECRET_KEY 派生 JWT_SECRET 与 ENCRYPTION_KEY。
    salt=None 安全：根密钥已是均匀随机，info 提供域分离。
    """
    return base64.urlsafe_b64encode(
        HKDF(algorithm=hashes.SHA256(), length=length, salt=None, info=context)
        .derive(root.encode())
    ).decode()


def _get_encryption_key() -> bytes:
    key = os.environ.get("ENCRYPTION_KEY", "")
    if not key:
        secret_key = os.environ.get("SECRET_KEY", "")
        if secret_key:
            # SECRET_KEY 根密钥派生（推荐路径，无告警）
            key = _derive_key(secret_key, b"encrypt")
        else:
            secret = os.environ.get("JWT_SECRET", "")
            if not secret:
                # P4 修复（2026-08-20 审计 B-质量面④）：原回落公开常量"quant-dev-secret-change-me"——
                # 读过源码的人即可解开全部"加密"凭证。改为进程内随机密钥（重启即孤儿化已加密数据，
                # 比"众人皆知"安全；响亮告警提示配 SECRET_KEY。实盘模式由 SD1 拒启动兜底）
                import secrets as _secrets
                _logger.critical("所有密钥均未设置——使用进程内随机密钥，"
                                 "重启后已加密凭证将无法解密！立即配置 SECRET_KEY（详见 .env）")
                secret = _secrets.token_urlsafe(48)
            _logger.warning("ENCRYPTION_KEY 未设置，从 JWT_SECRET 派生。"
                            "生产环境建议设置 SECRET_KEY（或 ENCRYPTION_KEY）环境变量")
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
