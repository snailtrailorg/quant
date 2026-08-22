"""#47 ENCRYPTION_KEY 迁移测试（2026-08-22）。

测试迁移脚本的核心逻辑：用旧密钥（JWT_SECRET 派生）加密 → 用新密钥迁移 → 新密钥解密一致。
SECRET_KEY 根密钥方案（2026-08-22）：新密钥从 SECRET_KEY → HKDF("encrypt") 派生。
"""
import os, base64, hashlib, json, pytest
from cryptography.fernet import Fernet
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from cryptography.hazmat.primitives import hashes

from src.quant_common.crypto import encrypt, decrypt, _get_encryption_key, _derive_key


@pytest.fixture(autouse=True)
def _reset_crypto_cache():
    """每测试前重置 _fernet 缓存，防跨测试 env 泄露。"""
    import src.quant_common.crypto as crypto_mod
    crypto_mod._fernet = None
    yield
    crypto_mod._fernet = None


def _old_derive_key(secret: str) -> str:
    """模拟旧密钥派生（sha256 回退）。"""
    return base64.urlsafe_b64encode(hashlib.sha256(secret.encode()).digest()).decode()


def _rand_key() -> str:
    import secrets
    return base64.urlsafe_b64encode(secrets.token_bytes(32)).decode()


def _rand_secret() -> str:
    import secrets
    return secrets.token_urlsafe(48)


class TestEncryptionKeyMigration:
    """验证旧密钥→新密钥迁移的完整性。"""

    def test_derive_key_consistency(self):
        """JWT_SECRET 派生密钥与 crypto._get_encryption_key 一致（无 ENCRYPTION_KEY 时）。"""
        k1 = _old_derive_key("test-secret")
        old_env = os.environ.pop("ENCRYPTION_KEY", None)
        os.environ["JWT_SECRET"] = "test-secret"
        try:
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
        old_key = _old_derive_key(old_secret)
        old_f = Fernet(old_key.encode())

        new_key = _rand_key()
        new_f = Fernet(new_key.encode())

        plain = {"api_key": "sk-xxx", "secret": "your-secret-123"}
        cipher = old_f.encrypt(json.dumps(plain, ensure_ascii=False).encode()).decode()

        decrypted = json.loads(old_f.decrypt(cipher.encode()).decode())
        re_encrypted = new_f.encrypt(json.dumps(decrypted, ensure_ascii=False).encode()).decode()

        final = json.loads(new_f.decrypt(re_encrypted.encode()).decode())
        assert final == plain

    def test_migration_non_reversible(self):
        """迁移后旧密钥无法解密新密文（正向安全）。"""
        old_key = _old_derive_key("old-secret")
        new_key = _rand_key()
        old_f = Fernet(old_key.encode())
        new_f = Fernet(new_key.encode())

        plain = "hello"
        cipher = new_f.encrypt(plain.encode()).decode()
        with pytest.raises(Exception):
            old_f.decrypt(cipher.encode())

    def test_multiple_credential_types(self):
        """兼容 JSON 对象与纯字符串两种加密内容。"""
        new_key = _rand_key()
        new_f = Fernet(new_key.encode())

        plain_str = "sk-xxxxxxxxxxxxxxxx"
        cipher_str = new_f.encrypt(plain_str.encode()).decode()
        assert new_f.decrypt(cipher_str.encode()).decode() == plain_str

        plain_json = {"api_key": "xxx", "api_secret": "yyy"}
        cipher_json = new_f.encrypt(json.dumps(plain_json, ensure_ascii=False).encode()).decode()
        assert json.loads(new_f.decrypt(cipher_json.encode()).decode()) == plain_json


class TestSecretKeyDerivation:
    """SECRET_KEY 根密钥派生测试（2026-08-22）。"""

    def test_hkdf_deterministic(self):
        """同一 SECRET_KEY + 同一 context → 始终输出相同密钥。"""
        root = _rand_secret()
        k1 = _derive_key(root, b"encrypt")
        k2 = _derive_key(root, b"encrypt")
        assert k1 == k2

    def test_domain_separation(self):
        """不同 context 派生不同密钥（jwt ≠ encrypt）。"""
        root = _rand_secret()
        jwt_key = _derive_key(root, b"jwt")
        enc_key = _derive_key(root, b"encrypt")
        assert jwt_key != enc_key

    def test_different_root_different_key(self):
        """不同 SECRET_KEY → 不同派生密钥。"""
        k1 = _derive_key(_rand_secret(), b"encrypt")
        k2 = _derive_key(_rand_secret(), b"encrypt")
        assert k1 != k2

    def test_derived_key_is_valid_fernet(self):
        """HKDF 派生密钥可用作 Fernet 密钥（44 字符 urlsafe_base64，32 字节）。"""
        root = _rand_secret()
        key = _derive_key(root, b"encrypt")
        f = Fernet(key.encode())
        cipher = f.encrypt(b"hello")
        assert f.decrypt(cipher) == b"hello"

    def test_secret_key_used_by_crypto(self):
        """SECRET_KEY 设置时 _get_encryption_key 返回 HKDF 派生值。"""
        root = _rand_secret()
        expected = _derive_key(root, b"encrypt")
        old_env = os.environ.pop("ENCRYPTION_KEY", None)
        old_jwt = os.environ.pop("JWT_SECRET", None)
        os.environ["SECRET_KEY"] = root
        try:
            import src.quant_common.crypto as crypto_mod
            crypto_mod._fernet = None
            assert crypto_mod._get_encryption_key().decode() == expected
        finally:
            if old_env is not None:
                os.environ["ENCRYPTION_KEY"] = old_env
            if old_jwt is not None:
                os.environ["JWT_SECRET"] = old_jwt
            else:
                os.environ.pop("SECRET_KEY", None)

    def test_secret_key_priority_over_jwt(self):
        """SECRET_KEY 优先于 JWT_SECRET（JWT_SECRET 存在时 SECRET_KEY 仍生效）。"""
        root = _rand_secret()
        expected = _derive_key(root, b"encrypt")
        old_env = os.environ.pop("ENCRYPTION_KEY", None)
        os.environ["SECRET_KEY"] = root
        os.environ["JWT_SECRET"] = "some-other-secret"
        try:
            import src.quant_common.crypto as crypto_mod
            crypto_mod._fernet = None
            assert crypto_mod._get_encryption_key().decode() == expected
        finally:
            if old_env is not None:
                os.environ["ENCRYPTION_KEY"] = old_env
            os.environ.pop("SECRET_KEY", None)
            os.environ.pop("JWT_SECRET", None)