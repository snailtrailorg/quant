#!/bin/bash
# ====================================================================
# install-flip-web.sh —— web 工件化批增量装位（2026-08-30，michael sudo 一次性）
# 仅三件：① quant-flip-web wrapper → /usr/local/sbin ② sudoers +白名单 ③ web 迁移三分支
# 幂等；不碰用户/密钥/单元（那些 bootstrap 已装过，增量装位不重跑全脚本）。
# 运行：sudo bash install-flip-web.sh          （wrapper 源自动探测：本地仓库/服务器 bundle）
# ====================================================================
set -euo pipefail
Q=${QUANT_DEPLOY_ROOT:-/data/websites/snailtrail.cc/quant}
WRAP_SRC=${WRAP_SRC:-}
if [ -z "$WRAP_SRC" ]; then
  for c in "/home/bernard/Projects/quant/deploy/wrappers" "/home/deploy/quant-bootstrap-bundle"; do
    [ -x "$c/quant-flip-web" ] && WRAP_SRC="$c" && break
  done
fi
[ -n "$WRAP_SRC" ] && [ -x "$WRAP_SRC/quant-flip-web" ] || { echo "❌ quant-flip-web 源不在位（本地仓库或 /home/deploy/quant-bootstrap-bundle）"; exit 1; }

install -o root -g root -m 755 "$WRAP_SRC/quant-flip-web" /usr/local/sbin/quant-flip-web
echo "✓ ① wrapper → /usr/local/sbin/quant-flip-web（源: $WRAP_SRC）"

SD=/etc/sudoers.d/quant-deploy
[ -f "$SD" ] || { echo "❌ $SD 不在位（先跑过 bootstrap?）"; exit 1; }
if grep -q 'quant-flip-web' "$SD"; then
  echo "✓ ② sudoers 已含 flip-web（no-op）"
else
  sed -i 's#/usr/local/sbin/quant-flip-server,#/usr/local/sbin/quant-flip-server, /usr/local/sbin/quant-flip-web,#' "$SD"
  visudo -cf "$SD" >/dev/null && echo "✓ ② sudoers +flip-web（visudo OK）"
fi

W="$Q/web"
if [ -L "$W" ]; then
  echo "✓ ③ web 链接已在（no-op）: $(readlink "$W")"
elif [ -d "$W" ]; then
  ts=$(date +%Y%m%d%H%M%S); legacy="$Q/releases/web-legacy-$ts"
  mkdir -p "$Q/releases"
  mv "$W" "$legacy/web" && touch "$legacy/.deployed"
  ln -sfn "$legacy/web" "$Q/web.tmp" && mv -T "$Q/web.tmp" "$Q/web"
  echo "✓ ③ web 实目录→ $legacy/web 并建链（毫秒窗；.deployed 防 GC 孤儿删）"
else
  echo "✓ ③ web 不存在（no-op；首次 release 建链）"
fi
echo "== 增量装位完成 =="
