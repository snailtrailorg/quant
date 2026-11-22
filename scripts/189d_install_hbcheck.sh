#!/usr/bin/env bash
# 批189 契约整改：新版 quant-hbcheck 装位（在**目标机**以 root/michael 执行）。
# 前置（控制机 bernard 已做）：两机 /tmp/quant-hbcheck 已由 ansible copy 投放；
#   sha256 = c2497c15a0b48b00a3a88104d3094fc4b1529220415e3d4cf99a73f9e4e79983
# 与上一批 quant-dbro 装位互不影响；两者都到位后再跑彩排/发布。
set -euo pipefail
SRC=/tmp/quant-hbcheck
DST=/usr/local/sbin/quant-hbcheck
[ -f "$SRC" ] || { echo "缺 $SRC（先让控制机 ansible copy）" >&2; exit 1; }
install -m 755 -o quant -g quant "$SRC" "$DST"
echo "装位完成：$DST"
sha256sum "$DST"
echo '期望：c2497c15a0b48b00a3a88104d3094fc4b1529220415e3d4cf99a73f9e4e79983  /usr/local/sbin/quant-hbcheck'
