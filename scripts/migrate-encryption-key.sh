#!/bin/bash
# #47 ENCRYPTION_KEY 独立密钥迁移脚本（2026-08-22）
#
# 用法:
#   bash scripts/migrate-encryption-key.sh <new-key>
#
# 步骤:
#   1. 从 .env 读 JWT_SECRET → 派生当前 Fernet 密钥（旧）
#   2. 遍历所有已加密列，用旧密钥解密
#   3. 用新密钥重新加密→写回各表
#   4. 设置 ENCRYPTION_KEY 到 .env（后续手动启服务）
#
# 安全: 迁移前先备份数据库（pg_dump -t broker_config ...）
#
# 生成新密钥:
#   python3 -c "import base64, secrets; print(base64.urlsafe_b64encode(secrets.token_bytes(32)).decode())"

set -e
cd "$(dirname "$0")/.."

if [ -z "$1" ]; then
    echo "用法: $0 <new-base64-urlsafe-fernet-key>"
    echo "生成: python3 -c \"import base64, secrets; print(base64.urlsafe_b64encode(secrets.token_bytes(32)).decode())\""
    exit 1
fi
NEW_KEY="$1"

# 检查是否在 venv 中
if [ -z "$VIRTUAL_ENV" ]; then
    if [ -d venv ]; then
        source venv/bin/activate
    else
        echo "未检测到 venv，请先 source venv/bin/activate"
        exit 1
    fi
fi

# 推入环境（供 Python 读取）
export ENCRYPTION_KEY_MIGRATE="$NEW_KEY"

exec python3 << 'PYEOF'
"""#47 ENCRYPTION_KEY 迁移：所有已加密列用旧密钥解密→新密钥重新加密→写回。

旧密钥派生：JWT_SECRET 的 sha256 → urlsafe_base64（32 字节），即 crypto._get_encryption_key()
在无 ENCRYPTION_KEY 环境变量时的行为。新密钥直接作为 Fernet 密钥（44 字节 urlsafe_base64）。
"""
import os, base64, hashlib, json, logging
from cryptography.fernet import Fernet

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("migrate-key")

# ── 旧密钥（当前派生）──
jwt = os.environ.get("JWT_SECRET", "")
if not jwt:
    log.error("JWT_SECRET 未设置或为空，无法派生旧密钥。从 .env 加载？")
    # 尝试读 .env
    env_path = os.path.join(os.path.dirname(__file__), "..", ".env")
    if os.path.exists(env_path):
        for line in open(env_path):
            if line.startswith("JWT_SECRET"):
                jwt = line.split("=", 1)[1].strip()
                break
if not jwt:
    log.warning("JWT_SECRET 仍为空——进程内随机密钥，所有已加密数据不可恢复。跳过迁移。")
    exit(1)

old_key = base64.urlsafe_b64encode(hashlib.sha256(jwt.encode()).digest()).decode()
old_f = Fernet(old_key.encode())

# ── 新密钥（用户提供）──
new_f = Fernet(os.environ["ENCRYPTION_KEY_MIGRATE"].encode())

# ── 已加密列清单 ──
TABLES = [
    ("broker_config", "credentials_encrypted", "id"),
    ("data_source_config", "credentials_encrypted", "id"),
    ("channel_config", "credentials_encrypted", "id"),
    ("im_bot_config", "credentials_encrypted", "id"),
    ("llm_model_config", "api_key_encrypted", "id"),
]

# system_config 的 smtp_password 行（value_type='password' 或 key='smtp_password'）
SYSTEM_CONFIG_KEY = "smtp_password"


def decrypt_val(f: Fernet, v: str) -> str:
    """统一解密入口；原文本未加密（如首次迁移时已有新密钥写入的部分行）跳过。"""
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
    # 1. 显式加密列
    for table, col, pk in TABLES:
        cur = conn.execute(f'SELECT {pk}, {col} FROM {table} WHERE {col} IS NOT NULL')
        rows = cur.fetchall()
        for row_pk, val in rows:
            if not val or val == "":
                continue
            plain = decrypt_val(old_f, val)
            if plain is None:
                continue
            new_val = encrypt_val(new_f, plain)
            conn.execute(f'UPDATE {table} SET {col}=%s WHERE {pk}=%s', (new_val, row_pk))
            total += 1
            log.info("  %s.%s id=%s 迁移成功", table, col, row_pk)

    # 2. system_config smtp_password
    cur = conn.execute("SELECT key, value FROM system_config WHERE key=%s", (SYSTEM_CONFIG_KEY,))
    row = cur.fetchone()
    if row and row[1]:
        val = row[1]
        plain = decrypt_val(old_f, val)
        if plain is not None:
            new_val = encrypt_val(new_f, plain)
            conn.execute("UPDATE system_config SET value=%s, updated_at=now() WHERE key=%s",
                         (new_val, SYSTEM_CONFIG_KEY))
            total += 1
            log.info("  system_config.smtp_password 迁移成功")
        else:
            log.warning("  system_config.smtp_password 解密失败，跳过（可能已是新密钥）")
    else:
        log.info("  system_config.smtp_password 无数据，跳过")

    conn.commit()

log.info("迁移完成，共 %d 条记录", total)
print("\n下一步：")
print(f"  1. 将 ENCRYPTION_KEY 写入 .env（或 system_config）:")
k = os.environ["ENCRYPTION_KEY_MIGRATE"]; print(f"     echo 'ENCRYPTION_KEY={k}' >> .env")
print("  2. 重启服务使新密钥生效")
print("  3. 验证：运行 tests/test_crypto_migration.py 或检查 /api/position 正常返回")
PYEOF