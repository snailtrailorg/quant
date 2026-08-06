#!/bin/bash
# init-env.sh - 交互式从模板生成 .env（避免手动 sed 出错）
# 用法（服务器）: sudo -u quant bash scripts/init-env.sh
set -euo pipefail

DIR="$(cd "$(dirname "$0")/.." && pwd)"
ENV="$DIR/.env"

echo "生成 $ENV（填入生产值）"
echo "---"

if [[ -f "$ENV" ]]; then
    read -rp "⚠️ $ENV 已存在，覆盖？(y/N) " ans
    [[ "$ans" == "y" ]] || { echo "取消"; exit 0; }
fi

read -rp "quant 数据库密码（必填，md5 认证）: " PGPWD
[[ -n "$PGPWD" ]] || { echo "❌ 密码必填"; exit 1; }

read -rp "Tushare token（必填）: " TOKEN
[[ -n "$TOKEN" ]] || { echo "❌ token 必填"; exit 1; }

read -rp "DeepSeek API key（可空，回车跳过）: " DS
read -rp "全量同步起点 YYYYMMDD（默认 20100101 全历史，约3GB）: " START
START=${START:-20100101}

echo "--- SMTP 邮件（邀请制用户管理：邀请开通/找回密码，可空回车走 DEV 模式打印） ---"
read -rp "SMTP_HOST（如 smtp.gmail.com，空则 DEV 模式）: " SMTP_HOST
SMTP_PORT=${SMTP_PORT:-587}
if [[ -n "$SMTP_HOST" ]]; then
    read -rp "SMTP_PORT（默认 587）: " SMTP_PORT_INPUT
    SMTP_PORT=${SMTP_PORT_INPUT:-587}
    read -rp "SMTP_USERNAME（如 snailtrail.org@gmail.com）: " SMTP_USERNAME
    read -rp "SMTP_PASSWORD（应用专用密码）: " SMTP_PASSWORD
    read -rp "SMTP_FROM（如 snailtrail.org@gmail.com）: " SMTP_FROM
fi

cat > "$ENV" <<EOF
# 生成的 .env（init-env.sh），勿提交
QUANT_DB_URL=postgresql://quant:${PGPWD}@127.0.0.1:5432/quant
VALKEY_URL=redis://127.0.0.1:6379/4
CELERY_BROKER_URL=redis://127.0.0.1:6379/5
CELERY_RESULT_BACKEND=redis://127.0.0.1:6379/6
TUSHARE_TOKEN=${TOKEN}
SYNC_START_DATE=${START}
DEEPSEEK_API_KEY=${DS}
DEEPSEEK_BASE_URL=https://api.deepseek.com/v1
GLM_API_KEY=
GLM_BASE_URL=https://open.bigmodel.cn/api/paas/v4
LARK_APP_ID=
LARK_APP_SECRET=
LARK_VERIFICATION_TOKEN=
SMTP_HOST=${SMTP_HOST}
SMTP_PORT=${SMTP_PORT}
SMTP_USERNAME=${SMTP_USERNAME}
SMTP_PASSWORD=${SMTP_PASSWORD}
SMTP_FROM=${SMTP_FROM}
EOF

chmod 600 "$ENV"
echo ""
echo "✅ $ENV 已生成（chmod 600）"
echo "⚠️ db 分配: VALKEY=db4, CELERY=db5/6（避开 safebox db0）"
echo "⚠️ 全量起点: $START（bar_1D 约 $([ "$START" == "20100101" ] && echo '3GB' || echo '更少')）"
echo "⚠️ SMTP: $([ -n "$SMTP_HOST" ] && echo "已配（$SMTP_HOST）" || echo "DEV 模式（打印不发）")"
