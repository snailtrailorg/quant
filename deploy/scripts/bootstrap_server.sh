#!/bin/bash
# ====================================================================
# bootstrap_server.sh —— 一次性加法安装（批3 设计稿 §5 步骤 2；幂等可增量重跑）
# 运行：服务器上 sudo bash bootstrap_server.sh（经 michael sudo 通道；root 不直连 ssh）
# 性质：纯加法零服务影响；此后常态部署=root-ssh 零依赖（deploy 密钥+sudoers 白名单）
# 前置：SRC 目录已就位（默认 /home/michael/3b2；web 工件化增量装位用
#       SRC=<dir> sudo bash bootstrap_server.sh 覆盖——dir 内须有 deploy/wrappers/ 全套
#       + deploy/templates/sudoers-quant-deploy.j2 + deploy/scripts/systemd/ 单元）
# 幂等说明（web 工件化批 2026-08-30 增量）：wrapper 重装=sudoers 重写=幂等覆盖；
#       web 迁移段三分支（链接在=no-op/目录在=迁移/不存在=no-op）。
# ====================================================================
set -euo pipefail
Q=/data/websites/snailtrail.cc/quant
SRC=${SRC:-/home/michael/3b2}

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
[ -s "$SRC/quant_deploy_ed25519.pub" ] && [ "$(wc -c < "$SRC/quant_deploy_ed25519.pub")" -gt 80 ] \
  || { echo "❌ 公钥缺失或异常（<80B）——防空串静默坑（2026-08-27 双盲审 P2）" >&2; exit 1; }
printf 'no-port-forwarding,no-agent-forwarding,no-pty %s\n' "$(cat "$SRC/quant_deploy_ed25519.pub")" \
  > /home/deploy/.ssh/authorized_keys
chown deploy:deploy /home/deploy/.ssh/authorized_keys
chmod 600 /home/deploy/.ssh/authorized_keys

echo "== 4 wrappers 装位（root 属主 755；quant-dbro 唯一 quant 属主）=="
install -m 755 -o root -g root \
  "$SRC"/quant-{svc,flip-server,flip-web,install-units,alembic-wrapper,importsmoke-wrapper,pip-wrapper,pinned} \
  /usr/local/sbin/
install -m 755 -o quant -g quant "$SRC/quant-dbro" "$SRC/quant-hbcheck" /usr/local/sbin/

echo "== 5 bin/run-current + releases/（deploy 属主）=="
mkdir -p "$Q/bin" "$Q/releases" "$Q/var"
install -m 755 -o root -g root "$SRC/run-current" "$Q/bin/run-current"
chown deploy:deploy "$Q/releases" "$Q/var"
# 首版回滚锚（六跑实锤）：手工 init 版无 .deployed 标记——阶段 6-8 失败时回滚目标
# 断言拒（"未经完整发布"），翻转后悬空。init 是合法已部署态，此处补标记。
for r in "$Q"/releases/*; do
  [ -d "$r" ] && touch "$r/.deployed"
done   # var=部署状态区（指纹/freeze 快照）——3b-1 曾建为 quant 属主，此处归位


echo "== 5.5 web 工件化迁移（2026-08-30 批;三分支幂等——B-P0-2）=="
W="$Q/web"
if [ -L "$W" ]; then
  echo "  web 链接已在（no-op）: $(readlink "$W")"
elif [ -d "$W" ]; then
  ts=$(date +%Y%m%d%H%M%S)
  legacy="$Q/releases/web-legacy-$ts"
  mkdir -p "$Q/releases"
  mv "$W" "$legacy/web" && touch "$legacy/.deployed"
  ln -sfn "$legacy/web" "$Q/web.tmp" && mv -T "$Q/web.tmp" "$Q/web"
  echo "  web 实目录→归位 $legacy 并建链（毫秒窗;B-P1-3 形态统一+.deployed 防 GC 孤儿删）"
else
  echo "  web 不存在（no-op;首次 release 建链）"
fi

echo "== 6 目标机前置（python3.11 已在=venv 同源；rsync assert）=="
/usr/bin/python3.11 --version
rsync --version | head -1

echo "== 7 权限验证（白名单过/越权拒/dbro 通）=="
sudo -u deploy sudo -n /usr/local/sbin/quant-svc status quant-md-hub@quant.service >/dev/null \
  && echo "✅ ① 白名单过" || { echo "❌ ① 白名单未过" >&2; exit 1; }
if sudo -u deploy sudo -n systemctl restart sshd >/dev/null 2>&1; then
  echo "❌ ② 越权竟然过了——sudoers 有洞，立即停用" >&2; exit 1
else
  echo "✅ ② 越权被拒"
fi
if UNITS=$(sudo -u deploy sudo -n -u quant /usr/local/sbin/quant-dbro live); then
  echo "✅ ③ dbro 通：$UNITS"
else
  echo "⚠️ ③ dbro 不通（PG peer/socket 路径——3b-2 首跑前核对项，见设计稿 P2-4）"
fi
echo "== bootstrap 完成（纯加法，未动任何运行中服务）=="
