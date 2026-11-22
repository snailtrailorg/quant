#!/usr/bin/env bash
# 批189 三分类拆分：新版 quant-dbro 装位（在**目标机**以 root/michael 执行）。
# 前置（控制机 bernard 已做）：deploy/.venv/bin/ansible <host> -m copy -a
#   "src=$PWD/deploy/wrappers/quant-dbro dest=/tmp/quant-dbro mode=0644"
#   两机 /tmp/quant-dbro sha256 均为 eb76d9dea5449c2127e8649280cdb36580452ab5ea37d671c8d6f624f5dc2caf
# 本脚本只做装位与自证，不碰 release；装位后再跑彩排/发布。
set -euo pipefail
SRC=/tmp/quant-dbro
DST=/usr/local/sbin/quant-dbro
[ -f "$SRC" ] || { echo "缺 $SRC（先让控制机 ansible copy）" >&2; exit 1; }
install -m 755 -o quant -g quant "$SRC" "$DST"
echo "装位完成：$DST"
sha256sum "$DST"
echo '期望：eb76d9dea5449c2127e8649280cdb36580452ab5ea37d671c8d6f624f5dc2caf  /usr/local/sbin/quant-dbro'
