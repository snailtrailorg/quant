#!/usr/bin/env python3
"""S110/S112 命中点上下文导出器（2026-09-29 技术债治理）。

只做一件事：把 ruff 的 except-pass/continue 清单变成「±5 行源码」，供人眼三分类
（A 真忽略加注释 / B 补日志 / C 该炸）。**不做自动分类**——批量改异常处理必须逐点读，
判断外包给启发式正是这次债的成因形态。

清单来自 ruff（免费精确 file/line），本脚本只负责可读化。
用法：cd server && venv/bin/python ../scripts/audit_except_pass.py
"""
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1] / "server"


def main() -> int:
    ruff = ROOT / "venv" / "bin" / "ruff"
    out = subprocess.run(
        [str(ruff), "check", "--select", "S110,S112", "--output-format", "json", "src"],
        cwd=ROOT, capture_output=True, text=True)
    if out.returncode not in (0, 1):   # 1=有命中（正常），其余=ruff 本身出错
        print(f"ruff 执行失败 rc={out.returncode}\n{out.stderr}", file=sys.stderr)
        return out.returncode
    findings = json.loads(out.stdout) if out.stdout.strip() else []
    by_file = {}
    for f in findings:
        by_file.setdefault(f["filename"], []).append(f["location"]["row"])
    if not by_file:
        print("无 S110/S112 命中（已干净）")
        return 0
    for fn, rows in sorted(by_file.items()):
        lines = Path(fn).read_text(encoding="utf-8").split("\n")
        print(f"\n===== {fn}（{len(rows)} 处）=====")
        for r in sorted(rows):
            lo, hi = max(0, r - 6), min(len(lines), r + 5)
            print(f"  --- 行 {r} ---")
            for i in range(lo, hi):
                marker = ">>>" if i + 1 == r else "   "
                print(f"  {marker} {i + 1:4d} {lines[i]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
