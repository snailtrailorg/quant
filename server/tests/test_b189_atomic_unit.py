"""批189 原子单元门禁（防「半切换」）。

规则：**M1 迁移 0192 一旦存在，代码侧不得再出现旧身份形态**（旧键写入/读点、SQL 读旧表）——
否则就是「schema 已切、代码还写旧键」的半切换：不报错、不告警、只丢单。

反过来，0192 不存在时本测试不拦（当前态），但检测器本身有自测，避免「永远不会红」的空门。
"""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "server" / "src"
MIG = sorted((ROOT / "server" / "migrations" / "versions").glob("0192_*.py"))

OLD_PATTERNS = [
    r"td:cmd:\{(?:int\(|account_id|_acct|aid)",
    r"td:exec:",
    r"quant:hb:md-hub",
    r"hub:bars:\{account_id\}",
    r"hub:latest_tick:\{account_id\}",
    r"hub:worker:max_ts:\{account_id\}",
    r"(FROM|JOIN|INTO|UPDATE)\s+trading_account\b",
]


def scan(text: str) -> list[str]:
    """返回命中行（跳过带「旧/退役/现状/迁移前」的说明行）。"""
    hits = []
    for i, line in enumerate(text.splitlines(), 1):
        if any(w in line for w in ("旧", "退役", "现状", "迁移前")):
            continue
        if any(re.search(p, line) for p in OLD_PATTERNS):
            hits.append(f"{i}: {line.strip()[:100]}")
    return hits


def test_detector_not_vacuous():
    """自测：检测器必须能抓到典型旧形态（防规则写废）。"""
    sample = 'r.lpush(f"td:cmd:{int(aid)}", x)\nr.exists(f"td:exec:{acct}")'
    assert len(scan(sample)) == 2


def test_no_old_identity_after_0192():
    if not MIG:
        return  # latch 未armed（当前态）
    bad = []
    for p in SRC.rglob("*.py"):
        if "__pycache__" in str(p):
            continue
        hits = scan(p.read_text(encoding="utf-8", errors="ignore"))
        bad += [f"{p.relative_to(ROOT)}:{h}" for h in hits]
    assert not bad, "0192 已存在但仍有旧身份形态（半切换）：\n" + "\n".join(bad[:20])


def test_new_mechanism_present_when_armed():
    """0192 存在 ⇒ 新键族必须已在代码里（防只删旧不建新）。"""
    if not MIG:
        return
    text = "".join(p.read_text(encoding="utf-8", errors="ignore")
                   for p in SRC.rglob("*.py") if "__pycache__" not in str(p))
    assert "iface:hb:" in text
