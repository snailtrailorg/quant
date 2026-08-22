"""#47 ENCRYPTION_KEY 迁移测试（2026-08-22）。

测试迁移脚本的核心逻辑：用旧密钥（JWT_SECRET 派生）加密 → 用新密钥迁移 → 新密钥解密一致。
"""
import os, base64, hashlib, json, pytest
from cryptography.fernet import Fernet

from src.quant_common.crypto import encrypt, decrypt, _get_encryption_key


def _derive_key(secret: str) -> str:
    """模拟旧密钥派生（crypto._get_encryption_key 在无 ENCRYPTION_KEY 时的行为）。"""
    return base64.urlsafe_b64encode(hashlib.sha256(secret.encode()).digest()).decode()


def _rand_key() -> str:
    import secrets
    return base64.urlsafe_b64encode(secrets.token_bytes(32)).decode()


class TestEncryptionKeyMigration:
    """验证旧密钥→新密钥迁移的完整性。"""

    def test_derive_key_consistency(self):
        """JWT_SECRET 派生密钥与 crypto._get_encryption_key 一致（无 ENCRYPTION_KEY 时）。"""
        k1 = _derive_key("test-secret")
        old_env = os.environ.pop("ENCRYPTION_KEY", None)
        os.environ["JWT_SECRET"] = "test-secret"
        try:
            # 清 _fernet 缓存强制重算
            import src.quant_common.crypto as crypto_mod
            crypto_mod._fernet = None
            k2 = crypto_mod._get_encryption_key().decode()
            assert k1 == k2, "派生密钥不一致"
        finally:
            if old_env is not None:
                os.environ["ENCRYPTION_KEY"] = old_env

    def test_roundtrip_migration(self):
        """旧密钥加密 → 用新密钥解密 → 原文明文不变。"""
        old_secret = "test-jwt-secret"
        old_key = _derive_key(old_secret)
        old_f = Fernet(old_key.encode())

        new_key = _rand_key()
        new_f = Fernet(new_key.encode())

        plain = {"api_key": "sk-xxx", "secret": "your-secret-123"}
        cipher = old_f.encrypt(json.dumps(plain, ensure_ascii=False).encode()).decode()

        # 模拟迁移：旧密钥解密 → 新密钥重加密
        decrypted = json.loads(old_f.decrypt(cipher.encode()).decode())
        re_encrypted = new_f.encrypt(json.dumps(decrypted, ensure_ascii=False).encode()).decode()

        # 新密钥可解密回原明文
        final = json.loads(new_f.decrypt(re_encrypted.encode()).decode())
        assert final == plain

    def test_migration_non_reversible(self):
        """迁移后旧密钥无法解密新密文（正向安全）。"""
        old_key = _derive_key("old-secret")
        new_key = _rand_key()
        old_f = Fernet(old_key.encode())
        new_f = Fernet(new_key.encode())

        plain = "hello"
        cipher = new_f.encrypt(plain.encode()).decode()
        with pytest.raises(Exception):
            old_f.decrypt(cipher.encode())

    def test_multiple_credential_types(self):
        """兼容 JSON 对象与纯字符串两种加密内容（LLM API key 存字符串，broker 存 JSON）。"""
        new_key = _rand_key()
        new_f = Fernet(new_key.encode())

        # 纯字符串（LLM api_key）
        plain_str = "sk-xxxxxxxxxxxxxxxx"
        cipher_str = new_f.encrypt(plain_str.encode()).decode()
        assert new_f.decrypt(cipher_str.encode()).decode() == plain_str

        # JSON 对象（broker 凭证）
        plain_json = {"api_key": "xxx", "api_secret": "yyy"}
        cipher_json = new_f.encrypt(json.dumps(plain_json, ensure_ascii=False).encode()).decode()
        assert json.loads(new_f.decrypt(cipher_json.encode()).decode()) == plain_json