#!/usr/bin/env python3
"""密钥迁移：从 ENCRYPTION_KEY 迁移到 SECRET_KEY→HKDF("encrypt") 派生。

用法:
  source venv/bin/activate
  python3 scripts/migrate_to_secret_key.py

要求环境变量: ENCRYPTION_KEY（旧密钥，当前在用）+ SECRET_KEY（新根密钥，目标）
如已设则自动读取 .env。
"""

import os
import base64
import json
import logging
from cryptography.fernet import Fernet
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from cryptography.hazmat.primitives import hashes

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("migrate-key")

# ── 1. 旧密钥：ENCRYPTION_KEY（当前正在用的）──
old_key = os.environ.get("ENCRYPTION_KEY", "")
if not old_key:
    # 从 .env 读
    env_path = os.path.join(os.path.dirname(__file__), "..", ".env")
    if os.path.exists(env_path):
        for line in open(env_path):
            if line.startswith("ENCRYPTION_KEY"):
                old_key = line.split("=", 1)[1].strip()
                break
if not old_key:
    log.error("ENCRYPTION_KEY 未设置，找不到旧密钥。退出。")
    exit(1)
old_f = Fernet(old_key.encode())

# ── 2. 新密钥：SECRET_KEY → HKDF("encrypt") ──
secret_key = os.environ.get("SECRET_KEY", "")
if not secret_key:
    env_path = os.path.join(os.path.dirname(__file__), "..", ".env")
    if os.path.exists(env_path):
        for line in open(env_path):
            if line.startswith("SECRET_KEY"):
                secret_key = line.split("=", 1)[1].strip()
                break
if not secret_key:
    log.error("SECRET_KEY 未设置。退出。")
    exit(1)

new_key = base64.urlsafe_b64encode(
    HKDF(algorithm=hashes.SHA256(), length=32, salt=None, info=b"encrypt")
    .derive(secret_key.encode())
).decode()
new_f = Fernet(new_key.encode())

# ── 3. 检查新旧是否一致 ──
if new_key == old_key:
    log.info("新旧密钥一致，无需迁移。你可以直接删除 ENCRYPTION_KEY 行，仅保留 SECRET_KEY。")
    exit(0)

log.info("新旧密钥不同，开始迁移...")

# ── 4. 已加密列清单 ──
TABLES = [
    ("broker_config", "credentials_encrypted", "id"),
    ("data_source_config", "credentials_encrypted", "id"),
    ("channel_config", "credentials_encrypted", "id"),
    ("im_bot_config", "credentials_encrypted", "id"),
    ("llm_model_config", "api_key_encrypted", "id"),
]
SYSTEM_CONFIG_KEY = "smtp_password"


def decrypt_val(f: Fernet, v: str) -> str | None:
    try:
        return f.decrypt(v.encode()).decode()
    except Exception as e:
        log.warning("  解密失败（可能已用新密钥？跳过）: %s", e)
        return None


def encrypt_val(f: Fernet, v: str) -> str:
    return f.encrypt(v.encode()).decode()


from src.data_platform.db import get_conn

total = 0
with get_conn() as conn:
    for table, col, pk in TABLES:
        cur = conn.execute(f"SELECT {pk}, {col} FROM {table} WHERE {col} IS NOT NULL")
        rows = cur.fetchall()
        for row_pk, val in rows:
            if not val or val == "":
                continue
            plain = decrypt_val(old_f, val)
            if plain is None:
                continue
            new_val = encrypt_val(new_f, plain)
            conn.execute(f"UPDATE {table} SET {col}=%s WHERE {pk}=%s", (new_val, row_pk))
            total += 1
            log.info("  %s.%s id=%s 迁移成功", table, col, row_pk)

    # system_config smtp_password
    cur = conn.execute("SELECT value FROM system_config WHERE key=%s", (SYSTEM_CONFIG_KEY,))
    row = cur.fetchone()
    if row and row[0]:
        plain = decrypt_val(old_f, row[0])
        if plain is not None:
            new_val = encrypt_val(new_f, plain)
            conn.execute(
                "UPDATE system_config SET value=%s, updated_at=now() WHERE key=%s",
                (new_val, SYSTEM_CONFIG_KEY),
            )
            total += 1
            log.info("  system_config.smtp_password 迁移成功")
    else:
        log.info("  system_config.smtp_password 无数据，跳过")

    conn.commit()

log.info("迁移完成，共 %d 条记录", total)
print("\n✅ 迁移完成后的部署步骤：")
print("  1. 编辑 .env，删除 ENCRYPTION_KEY 行")
print("  2. 重启服务: sudo systemctl restart quant-web-api@quant")
print("  3. 验证: 访问 /api/position 检查是否正常返回")