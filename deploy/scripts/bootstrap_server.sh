#!/bin/bash
# ====================================================================
# bootstrap_server.sh —— 3b-2 一次性加法安装（批3 设计稿 §5 步骤 2）
# 运行：服务器上 sudo bash bootstrap_server.sh（经 michael sudo 通道；root 不直连 ssh）
# 性质：纯加法零服务影响；此后常态部署=root-ssh 零依赖（deploy 密钥+sudoers 白名单）
# 前置：/home/michael/3b2/ 已 scp 进 9 wrapper+sudoers+9 单元+deploy 公钥
# ====================================================================
set -euo pipefail
Q=/data/websites/snailtrail.cc/quant
SRC=/home/michael/3b2

echo "== 1 deploy 用户（入 quant 组穿 750 父目录）=="
id deploy >/dev/null 2>&1 || useradd -m -s /bin/bash deploy
usermod -aG quant deploy

echo "== 2 sudoers（visudo -cf 校验 + requiretty 检查）=="
install -m 440 -o root -g root "$SRC/sudoers-quant-deploy.j2" /etc/sudoers.d/quant-deploy
visudo -cf /etc/sudoers.d/quant-deploy
if grep -rqi '^[^#].*requiretty' /etc/sudoers /etc/sudoers.d/ 2>/dev/null; then
  echo "❌ 检出 requiretty——会在 sudo 时卡死 ansible become，先处置再继续" >&2; exit 1
fi

echo "== 3 authorized_keys（限制项；from= 暂缓待出口 IP 确认）=="
install -d -m 700 -o deploy -g deploy /home/deploy/.ssh
printf 'no-port-forwarding,no-agent-forwarding,no-pty %s\n' "$(cat "$SRC/quant_deploy_ed25519.pub")" \
  > /home/deploy/.ssh/authorized_keys
chown deploy:deploy /home/deploy/.ssh/authorized_keys
chmod 600 /home/deploy/.ssh/authorized_keys

echo "== 4 wrappers 装位（root 属主 755；quant-dbro 唯一 quant 属主）=="
install -m 755 -o root -g root \
  "$SRC"/quant-{svc,flip-server,install-units,alembic-wrapper,importsmoke-wrapper,pip-wrapper,pinned} \
  /usr/local/sbin/
install -m 755 -o quant -g quant "$SRC/quant-dbro" /usr/local/sbin/

echo "== 5 bin/run-current + releases/（deploy 属主）=="
mkdir -p "$Q/bin" "$Q/releases"
install -m 755 -o root -g root "$SRC/run-current" "$Q/bin/run-current"
chown deploy:deploy "$Q/releases"

echo "== 6 目标机前置（python39/rsync）=="
dnf install -y python39 rsync >/dev/null 2>&1 || true
/usr/bin/python3.9 --version && rsync --version | head -1

echo "== 7 权限验证（白名单过/越权拒/dbro 通）=="
sudo -u deploy sudo -n /usr/local/sbin/quant-svc status quant-md-hub@quant.service >/dev/null \
  && echo "✅ ① 白名单过"
if sudo -u deploy sudo -n systemctl restart sshd >/dev/null 2>&1; then
  echo "❌ ② 越权竟然过了——sudoers 有洞，立即停用" >&2; exit 1
else
  echo "✅ ② 越权被拒"
fi
if UNITS=$(sudo -u deploy sudo -n /usr/local/sbin/quant-dbro live); then
  echo "✅ ③ dbro 通：$UNITS"
else
  echo "⚠️ ③ dbro 不通（PG peer/socket 路径——3b-2 首跑前核对项，见设计稿 P2-4）"
fi
echo "== bootstrap 完成（纯加法，未动任何运行中服务）=="
