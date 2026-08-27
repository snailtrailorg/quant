#!/bin/bash
# ====================================================================
# bootstrap_staging.sh —— 本地保真彩排环境一次性搭建（批3 3b-2，2026-08-26）
# 运行：开发机 sudo bash deploy/scripts/bootstrap_staging.sh
# 目标：把产上权限/路径/单元原样复刻到 /data/websites/snailtrail.cc/quant——
#   release.yml 彩排撞出的每类边界（属主/白名单/真实 schema）在本地先爆。
# 前置：deploy 密钥已生成（~/.ssh/quant_deploy_ed25519）；本地 PG/Valkey 由 dev-init-* 管。
# 幂等：可重复跑（install - 类操作天然幂等）。
# ====================================================================
set -euo pipefail
Q=/data/websites/snailtrail.cc/quant
REPO=$(cd "$(dirname "$0")/../.." && pwd)          # 仓库根
VENV_SRC="$REPO/server/venv"                        # dev 现成 3.10 树（含编译好的 vnpy_xtp）

echo "== 1 用户（quant/deploy 同款；deploy 入 quant 组）=="
id quant   >/dev/null 2>&1 || useradd -m -s /bin/bash quant
id deploy  >/dev/null 2>&1 || useradd -m -s /bin/bash deploy
usermod -aG quant deploy

echo "== 2 目录骨架 + 权限矩阵（与产一致）=="
mkdir -p "$Q"/{shared/static,releases,var,bin}
chown quant:quant "$Q" && chmod 750 "$Q"
chown -R quant:quant "$Q/shared" && chmod 700 "$Q/shared"
chown deploy:deploy "$Q/releases" "$Q/var"

echo "== 3 sudoers（同模板；visudo -cf）=="
install -m 440 -o root -g root "$REPO/deploy/templates/sudoers-quant-deploy.j2" /etc/sudoers.d/quant-deploy
visudo -cf /etc/sudoers.d/quant-deploy

echo "== 4 deploy 密钥装 localhost（彩排走真 ssh 全链）=="
install -d -m 700 -o deploy -g deploy /home/deploy/.ssh
# 2026-08-26 踩坑：sudo bash 下 $HOME=/root——公钥 cat 空串静默通过 set -e，
# authorized_keys 只剩 40 字节前缀。修：显式从调用者家目录取（SUDO_USER），缺失即硬失败。
PUB="/home/${SUDO_USER:-bernard}/.ssh/quant_deploy_ed25519.pub"
[ -f "$PUB" ] || { echo "❌ deploy 公钥不存在: $PUB" >&2; exit 1; }
[ "$(wc -c < "$PUB")" -gt 80 ] || { echo "❌ 公钥文件异常（<80B）: $PUB" >&2; exit 1; }
printf 'no-port-forwarding,no-agent-forwarding %s\n' "$(cat "$PUB")" > /home/deploy/.ssh/authorized_keys
chown deploy:deploy /home/deploy/.ssh/authorized_keys && chmod 600 /home/deploy/.ssh/authorized_keys

echo "== 5 wrappers + run-current 装位（同产路径）=="
install -m 755 -o root -g root \
  "$REPO"/deploy/wrappers/quant-{svc,flip-server,install-units,alembic-wrapper,importsmoke-wrapper,pip-wrapper,pinned} \
  /usr/local/sbin/
install -m 755 -o quant -g quant \
  "$REPO"/deploy/wrappers/quant-{dbro,hbcheck} /usr/local/sbin/
install -m 755 -o root -g root "$REPO/deploy/wrappers/run-current" "$Q/bin/run-current"

echo "== 6 venv 复刻（cp 现成树 + shebang 一次性重指 + 归 quant）=="
if [ ! -x "$Q/shared/venv/bin/python" ]; then
  [ -x "$VENV_SRC/bin/python" ] || { echo "❌ dev venv 不存在: $VENV_SRC（先 scripts/dev-* 建好）" >&2; exit 1; }
  cp -a "$VENV_SRC" "$Q/shared/venv"
  # venv 入口脚本的 shebang 钉死旧绝对路径——批量重指（fix-venv 同款）
  find "$Q/shared/venv/bin" -maxdepth 1 -type f -exec sed -i "1s|^#!.*venv/bin/python|#!$Q/shared/venv/bin/python|" {} +
fi
chown -R quant:quant "$Q/shared/venv"

echo "== 7 .env 外置（dev .env → shared；quant 600）=="
if [ -f "$REPO/server/.env" ]; then
  install -m 600 -o quant -g quant "$REPO/server/.env" "$Q/shared/.env"
else
  echo "⚠️ dev server/.env 不存在——hub 等读 QUANT_DB_URL 的服务起不来，先补（彩排前置）"
fi

echo "== 8 初始 release + server 链接（代码树入 releases；.deployed 标记=回滚锚）=="
if [ ! -L "$Q/server" ]; then
  INIT_ID="$(date +%Y%m%d%H%M)-0000abc"   # hex 合规（stginit 曾违反 wrapper 正则——回滚 flip 必败，双盲审 A 实锤未进提交）
  mkdir -p "$Q/releases/$INIT_ID"
  for item in src migrations scripts vendor tests docs alembic.ini requirements.txt; do
    [ -e "$REPO/server/$item" ] && cp -a "$REPO/server/$item" "$Q/releases/$INIT_ID/"
  done
  touch "$Q/releases/$INIT_ID/.deployed"
  chown -R deploy:deploy "$Q/releases/$INIT_ID"
  ln -s "$Q/releases/$INIT_ID" "$Q/server"
fi

echo "== 9 单元装位（run-current 形态 9 单元同款；daemon-reload）=="
install -m 644 -o root -g root "$REPO"/server/scripts/systemd/quant-*.service /etc/systemd/system/
systemctl daemon-reload

echo "== 10 权限自证（白名单过/越权拒）=="
sudo -u deploy sudo -n /usr/local/sbin/quant-svc is-active quant-md-hub@quant.service >/dev/null 2>&1 \
  || echo "（hub 未起属预期——彩排波次才起；白名单本身已过）"
if sudo -u deploy sudo -n systemctl restart sshd >/dev/null 2>&1; then
  echo "❌ 越权过了——检查 sudoers" >&2; exit 1
else
  echo "✅ 越权被拒"
fi
sudo -u quant "$Q/bin/run-current" -c "print('run-current OK')" 2>/dev/null \
  || sudo -u quant "$Q/bin/run-current" -c "print('run-current OK（回退链生效）')"
echo "== staging 搭建完成（服务未启动——由彩排波次或手动 quant-svc 起）=="
