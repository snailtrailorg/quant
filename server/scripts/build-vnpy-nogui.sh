#!/usr/bin/env bash
# 批 125：vnpy nogui wheel 重打脚本（上游升版时跑——fork 治理的唯一入口）
# 用法: bash scripts/build-vnpy-nogui.sh [上游版本号，默认 4.5.0]
set -euo pipefail
VER="${1:-4.5.0}"
WORK=$(mktemp -d)
trap 'rm -rf "$WORK"' EXIT
pip download "vnpy==$VER" --no-deps -d "$WORK" -q -i https://pypi.tuna.tsinghua.edu.cn/simple
cd "$WORK"
python3 - <<PYEOF
import zipfile, pathlib
src_wheel = next(pathlib.Path('.').glob('vnpy-*.whl'))
with zipfile.ZipFile(src_wheel) as z:
    z.extractall('src')
di = next(pathlib.Path('src').glob('vnpy-*.dist-info'))
meta = di / 'METADATA'
lines = meta.read_text(encoding='utf-8').splitlines(keepends=True)
drop = ['pyside6', 'pyqtgraph', 'qdarkstyle', 'qrcode']
out = [ln for ln in lines
       if not (ln.startswith('Requires-Dist:') and any(d in ln.lower() for d in drop))]
assert len(out) < len(lines), "未删任何行——上游可能已去掉 GUI pin（确认后直接用官方 wheel 即可）"
meta.write_text(''.join(out), encoding='utf-8')
new_di = di.parent / f"vnpy-{VER}+nogui.dist-info"
di.rename(new_di)
rec = new_di / 'RECORD'
rec.write_text(rec.read_text(encoding='utf-8').replace(di.name, new_di.name), encoding='utf-8')
with zipfile.ZipFile(f'vnpy-{VER}+nogui-py3-none-any.whl', 'w', zipfile.ZIP_DEFLATED) as z:
    for p in sorted(pathlib.Path('src').rglob('*')):
        if p.is_file():
            z.write(p, p.relative_to('src').as_posix())
print(f"✓ {new_di.name.replace('.dist-info','')} 重打完成")
PYEOF
cp "vnpy-$VER+nogui-py3-none-any.whl" "$OLDPWD/deps/"
echo "✓ 已放入 deps/ —— 更新 requirements.txt 指向后跑 lock-sync"
