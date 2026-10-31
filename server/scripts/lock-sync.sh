#!/bin/bash
# lock-sync.sh - 依赖变更安全同步：requirements.txt → requirements.lock（带 hash 重编译）
#
# 背景：lock 是部署唯一真源（quant-pip-wrapper --require-hashes），但重生成 lock
# 必须带 --generate-hashes——2026-10-11 事故（裸 pip-compile 丢全部 3431 个 sha256，
# 部署 hash 门双双拦截）后立的规约固化为本脚本。改依赖的唯一正道：
#   1. 编辑 requirements.txt（加/改/删直接依赖行）
#   2. bash scripts/lock-sync.sh   ← 本脚本编译 lock + 双重校验
#   3. git add requirements.txt requirements.lock && git commit
#
# 校验（不过即 exit 1，防半成品 lock 进仓）：
#   - lock 每个钉版行都带 --hash=sha256（缺 hash = 部署必炸）
#   - 本机 dry-run --require-hashes 全解析（与部署管道同参数）
#   - 直接依赖全部出现在 lock（防 typo 漏包）
set -euo pipefail

DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$DIR"
REQ="requirements.txt"
LOCK="requirements.lock"

command -v ./venv/bin/pip-compile >/dev/null || {
    echo "❌ venv 缺 pip-compile（pip-tools）——装：./venv/bin/pip install pip-tools" >&2
    exit 1
}

[[ -f "$REQ" ]] || { echo "❌ 缺 $REQ" >&2; exit 1; }

# UPGRADE=1 forward-roll: bump resolved versions to latest stable
UPGRADE_FLAG=""
[ "${UPGRADE:-0}" = "1" ] && UPGRADE_FLAG="--upgrade"
echo "① 重编译 $LOCK（--generate-hashes）..."
./venv/bin/pip-compile --generate-hashes --no-strip-extras $UPGRADE_FLAG \
    --output-file "$LOCK" "$REQ"

echo "② 校验 hash 完整性..."
n_pkg=$(grep -cE '^[a-zA-Z0-9]' "$LOCK")
n_hash=$(grep -c 'sha256:' "$LOCK")
if [[ "$n_hash" -lt "$n_pkg" ]]; then
    echo "❌ $LOCK 有钉版行缺 hash（$n_pkg 行 / $n_hash hash）——不得提交！" >&2
    exit 1
fi
echo "   ✓ $n_pkg 行全带 hash"

echo "③ dry-run --require-hashes 解析校验（与部署管道同参数）..."
./venv/bin/pip install --dry-run --require-hashes \
    -i "${QUANT_PIP_INDEX:-https://pypi.tuna.tsinghua.edu.cn/simple}" \
    -r "$LOCK" >/dev/null
echo "   ✓ 解析通过"

echo "④ 直接依赖全在 lock..."
miss=0
while IFS= read -r line; do
    [[ "$line" =~ ^[a-zA-Z] ]] || continue
    name=$(echo "$line" | sed 's/\[.*//; s/[<>=!~; ].*//' | tr '[:upper:]' '[:lower:]' | tr '_' '-')
    [[ -n "$name" ]] || continue
    grep -qiE "^${name//+/\\+}(\[[^]]*\])?(==| @ )" "$LOCK" || { echo "   ❌ $name 不在 lock（typo？）"; miss=1; }
done < "$REQ"
[[ "$miss" -eq 0 ]] || exit 1
echo "   ✓ 全部命中"

echo "⑤ marker 档守卫（批 148 审 B2 P0：3.10 重编译会把 >=3.11 marker 档整体剔除）..."
miss=0
while IFS= read -r line; do
    [[ "$line" =~ python_version ]] || continue
    name=$(echo "$line" | sed 's/\[.*//; s/[<>=!~; ].*//' | tr '[:upper:]' '[:lower:]' | tr '_' '-')
    marker=$(echo "$line" | sed -n 's/.*;[[:space:]]*\(python_version.*\)$/\1/p')
    [[ -n "$name" && -n "$marker" ]] || continue
    ver=$(echo "$marker" | grep -oE '[0-9]+\.[0-9]+' | head -1)
    op=$(echo "$marker" | grep -oE '(>=|<)' | head -1)
    [[ -n "$ver" && -n "$op" ]] || continue
    grep -qiE "^${name//+/\+}(\[[^]]*\])?==.*python_version[[:space:]]+${op}[[:space:]]+\"?${ver}" "$LOCK" \
        || { echo "   X $name 的 ${op}${ver} 方向档在 lock 缺失——多目标档被剔除！" >&2; miss=1; }
done < "$REQ"
[[ "$miss" -eq 0 ]] || exit 1
echo "   ✓ marker 档完整"

echo
echo "✅ lock 同步完成。下一步："
echo "   git add requirements.txt requirements.lock && git commit"
