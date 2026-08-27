#!/usr/bin/env bash
# 初始化量化平台 PostgreSQL（本地 dev）
# - quant 角色专属 trust 免密（其他 app 认证不变）
# - 无需 postgres 密码：临时翻 trust → 建角色/库/扩展 → 还原 + 留 quant 专属行
# 用法:  sudo bash scripts/dev-init-db.sh
set -euo pipefail

# --- 找 pg_hba.conf ---
HBA_FILE=$(find /var/lib/pgsql -name pg_hba.conf -type f -print -quit 2>/dev/null || true)
[ -n "$HBA_FILE" ] || { echo "✗ 找不到 pg_hba.conf（预期 /var/lib/pgsql/ 下）"; exit 1; }
echo "→ pg_hba.conf: $HBA_FILE"

ORIG_BAK="${HBA_FILE}.orig.$(date +%Y%m%d%H%M%S)"
cp -a "$HBA_FILE" "$ORIG_BAK"
echo "  原始备份: $ORIG_BAK"

reload_pg() { systemctl reload postgresql; }

# 条件轮询（P2-2）：等的是 reload 后 hba 规则生效（psql 免密连上才算好），
# 不是"PG 活着"——pg_isready 恒首拍即真，等价没等。
wait_psql() {  # 用法: wait_psql -U postgres -d postgres
  local i
  for i in $(seq 1 10); do
    if psql "$@" -c 'select 1' >/dev/null 2>&1; then return 0; fi
    sleep 0.5
  done
  echo "✗ 等待 psql $* 就绪超时（5s）——hba 未生效？若中途退出请手动还原: cp $HBA_FILE.orig.* $HBA_FILE" >&2
  return 1
}

# --- 1. 临时：顶部插 all all trust（仅供本次 superuser 操作）---
echo "→ 临时翻 trust（仅本步用）..."
{ printf '# quant-temp-trust (init only)\nlocal all all trust\nhost all all 127.0.0.1/32 trust\nhost all all ::1/128 trust\n# end quant-temp-trust\n\n'; cat "$HBA_FILE"; } > "$HBA_FILE.tmp"
cat "$HBA_FILE.tmp" > "$HBA_FILE" && rm -f "$HBA_FILE.tmp"
reload_pg
wait_psql -U postgres -d postgres

# --- 2. superuser 建 quant 角色/库/扩展（trust 可免密连 postgres）---
echo "→ 建 quant 角色/库 + pgvector ..."
psql -U postgres -d postgres -v ON_ERROR_STOP=1 <<'SQL'
DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname='quant') THEN
    CREATE ROLE quant LOGIN;
  END IF;
END
$$;
SQL
psql -U postgres -d postgres -c "CREATE DATABASE quant OWNER quant;" 2>/dev/null && echo "  库 quant 已创建" || echo "  库 quant 已存在"
if psql -U postgres -d quant -c "CREATE EXTENSION IF NOT EXISTS vector;" 2>/dev/null; then
  echo "  pgvector 已启用 ✓"
else
  echo "  ✗ pgvector 扩展不可用，请装: sudo dnf install pgvector" >&2
  # 还原再退出
  cp -a "$ORIG_BAK" "$HBA_FILE"; reload_pg
  exit 1
fi

# --- 3. 还原原始 pg_hba + 只加 quant 专属 trust 行 ---
echo "→ 还原原始认证 + 加 quant 专属 trust 行（其他 app 不受影响）..."
{ printf '# quant-platform-trust (local dev passwordless, quant role/db only)\nlocal quant quant trust\nhost quant quant 127.0.0.1/32 trust\nhost quant quant ::1/128 trust\n# end quant-platform-trust\n\n'; cat "$ORIG_BAK"; } > "$HBA_FILE.tmp"
cat "$HBA_FILE.tmp" > "$HBA_FILE" && rm -f "$HBA_FILE.tmp"
reload_pg

# --- 4. 验证 quant 免密（真条件轮询：hba 行生效才连得上，10×0.5s）---
ok=1
for _ in $(seq 1 10); do
  if psql -U quant -d quant -c "select current_user, current_database();" >/dev/null 2>&1; then
    echo "✓ quant 免密连接成功"
    ok=0
    break
  fi
  sleep 0.5
done
if [ "$ok" -ne 0 ]; then
  echo "✗ quant 连接失败（5s），检查上方输出" >&2
  exit 1
fi

echo ""
echo "✓ PG 初始化完成"
echo "  角色: quant（本地免密，仅 quant 角色/库）"
echo "  库:   quant"
echo "  pgvector: 已启用"
echo "  连接: postgresql://quant@127.0.0.1:5432/quant"
echo "  其他 app 的 PG 认证已还原原样，不受影响"