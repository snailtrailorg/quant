#!/usr/bin/env bash
# wd-20 §2.3-C · 令牌防回退门：内联 px/hex 计数对比基线（只许降不许升）
# 基线随批收紧（--update 落库）；两类豁免：#defa*（K 线数据面色，grep 排除不计）；Avatar.vue 调色板 6 hex（仍计数，但属身份区分色——允许存在、不要求令牌化，wd-20 §2.3-C）
# 用法：bash scripts/check-tokens.sh [更新基线] —— CI/build 前钩子挂 exit 码
set -u
cd "$(dirname "$0")/.."
BASE_FILE=web/scripts/.token-baseline
count_px()   { grep -rEo "(margin|padding): ?[0-9]+px" web/src/views/ web/src/components/ --include="*.vue" 2>/dev/null | wc -l; }
# hex 排除 K 线数据面色（#defa*——图表数据非 UI 语义色）
count_hex()  { grep -rEoh "#[0-9a-fA-F]{3,6}" web/src/views/ web/src/components/ --include="*.vue" 2>/dev/null | grep -v "^#defa" | wc -l; }
# 批24：字号旁路第三维（px/em/rem/% 相对单位同属排版旁路；驼峰 fontSize=编辑器/图表/头像 API 参数非 CSS 排版，天然不匹配）
count_fs()   { grep -rEo "font-size: ?[0-9.]+(px|em|rem|%)" web/src/views/ web/src/components/ web/src/layouts/ web/src/App.vue --include="*.vue" 2>/dev/null | wc -l; }
PX=$(count_px); HEX=$(count_hex); FS=$(count_fs)
if [ "${1:-}" = "--update" ]; then
  echo "BASE_PX=$PX BASE_HEX=$HEX BASE_FS=$FS" > "$BASE_FILE"; echo "基线更新: PX=$PX HEX=$HEX FS=$FS"; exit 0
fi
if [ ! -f "$BASE_FILE" ]; then echo "BASE_PX=$PX BASE_HEX=$HEX BASE_FS=$FS" > "$BASE_FILE"; echo "首采基线: PX=$PX HEX=$HEX FS=$FS"; exit 0; fi
source "$BASE_FILE"
: "${BASE_FS:=0}"   # 旧基线无 FS 键时兜底 0（批24 前基线只有 PX/HEX 两键）
RC=0
[ "$PX" -gt "$BASE_PX" ] && { echo "✗ 内联 px 计数上升: $PX > $BASE_PX（令牌禁新增——wd-20 §2.3-C）"; RC=1; }
[ "$HEX" -gt "$BASE_HEX" ] && { echo "✗ 内联 hex 计数上升: $HEX > $BASE_HEX（色值禁新增）"; RC=1; }
[ "$FS" -gt "$BASE_FS" ] && { echo "✗ font-size 硬编码上升: $FS > $BASE_FS（字号唯一真相源=六级令牌——批24）"; RC=1; }
[ "$RC" -eq 0 ] && echo "✓ 令牌门: PX=$PX≤$BASE_PX HEX=$HEX≤$BASE_HEX FS=$FS≤$BASE_FS"
exit $RC
