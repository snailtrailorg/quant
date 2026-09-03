#!/usr/bin/env bash
# wd-20 §2.3-C · 令牌防回退门：内联 px/hex 计数对比基线（只许降不许升）
# 基线首采 2026-09-03：px=59 hex=131（K 线数据面色 #defa* 不计——图表数据非 UI 语义色）
# 用法：bash scripts/check-tokens.sh [更新基线] —— CI/build 前钩子挂 exit 码
set -u
cd "$(dirname "$0")/.."
BASE_FILE=web/scripts/.token-baseline
count_px()   { grep -rEo "(margin|padding): ?[0-9]+px" web/src/views/ web/src/components/ --include="*.vue" 2>/dev/null | wc -l; }
# hex 排除 K 线数据面色（#defa*——图表数据非 UI 语义色）
count_hex()  { grep -rEoh "#[0-9a-fA-F]{3,6}" web/src/views/ web/src/components/ --include="*.vue" 2>/dev/null | grep -v "^#defa" | wc -l; }
PX=$(count_px); HEX=$(count_hex)
if [ "${1:-}" = "--update" ]; then
  echo "BASE_PX=$PX BASE_HEX=$HEX" > "$BASE_FILE"; echo "基线更新: PX=$PX HEX=$HEX"; exit 0
fi
if [ ! -f "$BASE_FILE" ]; then echo "BASE_PX=$PX BASE_HEX=$HEX" > "$BASE_FILE"; echo "首采基线: PX=$PX HEX=$HEX"; exit 0; fi
source "$BASE_FILE"
RC=0
[ "$PX" -gt "$BASE_PX" ] && { echo "✗ 内联 px 计数上升: $PX > $BASE_PX（令牌禁新增——wd-20 §2.3-C）"; RC=1; }
[ "$HEX" -gt "$BASE_HEX" ] && { echo "✗ 内联 hex 计数上升: $HEX > $BASE_HEX（色值禁新增）"; RC=1; }
[ "$RC" -eq 0 ] && echo "✓ 令牌门: PX=$PX≤$BASE_PX HEX=$HEX≤$BASE_HEX"
exit $RC
