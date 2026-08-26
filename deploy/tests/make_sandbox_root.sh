#!/usr/bin/env bash
# make_sandbox_root.sh —— 生成/重置 3a 沙箱树（deploy/tests/sandbox_root/）
#
# 结构:
#   sandbox_root/quant/{bin,releases,shared/{runtime},var}   —— 部署根（prod 同形）
#   sandbox_root/stage/                                       —— 发布源（fixtures/baseline 占位符替换；场景注入点）
#   quant/shared/.env                                         —— 沙箱服务环境（占位值）
#   quant/shared/venv/                                        —— 真 venv（alembic/sqlalchemy/psycopg，场景 2 真链路）
#   quant/bin/run-current                                     —— 与生产同源（deploy/run-current）
#
# 场景 2 数据面（设计稿 §6 道具化真验）: 开发机本地 PG（dev-init-db.sh 机制 = sudo 翻 pg_hba，
# 本机禁 sudo 不通；但 quant 角色/库已就绪且 trust 免密）——在 quant 库内重置 sbx_deploy schema，
# 真 alembic 走真 PG18 连接（非 sqlite 假 alembic，高于任务降级预案的保真度）。
#
# 用法: bash deploy/tests/make_sandbox_root.sh
set -euo pipefail

# 双盲审修补: 互斥锁——rm -rf 整棵沙箱树非原子，防与 run_scenarios/另一实例并发互踩
# （独立锁文件，不与 run_scenarios.sh 互锁——后者串行调用本脚本）
exec 9>"$(dirname "$0")/.make_sandbox_root.lock"
flock -n 9 || { echo "✗ 已有 make_sandbox_root 实例在跑（flock 拒并发）" >&2; exit 9; }

HERE=$(cd "$(dirname "$0")" && pwd)            # deploy/tests
FIX=$HERE/fixtures/baseline
SBX=$HERE/sandbox_root
ROOT=$SBX/quant
STAGE=$SBX/stage

# 沙箱 venv 依赖 pin（2026-08-26 清华镜像实测定案；全局 pip wheel 缓存使重复安装 ~14s）
SBX_VENV_PYTHON=python3.10
SBX_VENV_PINS="alembic==1.19.1 SQLAlchemy==2.0.52 psycopg==3.3.4 psycopg-binary==3.3.4"
SBX_PIP_INDEX=https://pypi.tuna.tsinghua.edu.cn/simple

# --- 0) 停掉上一轮沙箱单元（rm 树前必停——进程 cwd 指向被删树会变僵尸态） ---
systemctl --user stop quant-sbx-web.service quant-sbx-celery.service quant-sbx-hub.service 2>/dev/null || true
systemctl --user reset-failed 'quant-sbx-*' 2>/dev/null || true

# --- 1) 树骨架 ---
rm -rf "$SBX"
mkdir -p "$ROOT/bin" "$ROOT/releases" "$ROOT/shared/runtime" "$ROOT/var" "$STAGE"

# --- 2) staging 基线（__SBX_ROOT__ 占位符替换为实际沙箱根） ---
cp -a "$FIX/." "$STAGE/"
grep -rl '__SBX_ROOT__' "$STAGE" | while IFS= read -r f; do
  sed -i "s|__SBX_ROOT__|$ROOT|g" "$f"
done

# --- 3) shared/.env（沙箱服务环境；形态对齐生产 quant:quant 600） ---
cat > "$ROOT/shared/.env" <<EOF
# 沙箱 .env（占位值；生产对应真实 DB/Valkey 连串，属 quant 600，deploy 不可读）
QUANT_ALEMBIC_URL=postgresql+psycopg://quant@127.0.0.1:5432/quant
QUANT_ALEMBIC_SCHEMA=sbx_deploy
SBX_WEB_PORT=18923
SBX_HB_FILE=$ROOT/shared/runtime/hub_hb.json
EOF
chmod 600 "$ROOT/shared/.env"

# --- 4) run-current（与生产同源，单一真相） ---
install -m 755 "$HERE/../run-current" "$ROOT/bin/run-current"

# --- 5) shared/venv（真 venv；每次重建——pip 走全局 wheel 缓存，网络只冷一次） ---
"$SBX_VENV_PYTHON" -m venv "$ROOT/shared/venv"
# shellcheck disable=SC2086
"$ROOT/shared/venv/bin/pip" install -q -i "$SBX_PIP_INDEX" $SBX_VENV_PINS

# --- 6) PG 沙箱 schema 重置（quant 库内 sbx_deploy；场景间零残留） ---
psql -U quant -h 127.0.0.1 -d quant -v ON_ERROR_STOP=1 \
  -c "DROP SCHEMA IF EXISTS sbx_deploy CASCADE" \
  -c "CREATE SCHEMA sbx_deploy" >/dev/null
echo "✓ 沙箱树就绪: $ROOT"
echo "  staging: $STAGE（场景注入点）"
echo "  venv:    $ROOT/shared/venv（alembic 真链路）"
echo "  PG:      quant 库 sbx_deploy schema 已重置"
