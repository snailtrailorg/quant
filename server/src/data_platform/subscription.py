"""批 172d P3 · 行情授权门（data_license）。

规则：`AccountPermSpec.data_license` 为空 = 不限制（平台默认，零回归）；
非空 = 逗号分隔白名单，元素可以是品种类别（stock/listed_fund/convertible/fund/reits/perp）、
精确 vt_symbol（600000.SHSE）或 `*`（全放行）。

未知标的（security_master 无行）→ 拒绝（fail-closed，与开仓规则一致）。
"""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


def _data_license(account: str) -> str:
    """data_license 真源：md-* 取 market_interface.params（M4）；其余走 AccountPermSpec（TD）。"""
    if str(account).startswith("md-"):
        import json
        from src.data_platform.db import get_conn
        with get_conn() as c:
            row = c.execute("SELECT params FROM market_interface WHERE code=%s", (account,)).fetchone()
        if not row:
            return ""
        p = row[0] or {}
        if isinstance(p, str):
            p = json.loads(p)
        return str(p.get("data_license") or "")
    from src.data_platform import specs
    return str(specs.AccountPermSpec.of(account).data_license or "")


def licensed(account_id: int, symbol: str) -> bool:
    try:
        raw = _data_license(account_id).strip()
        if not raw:
            return True
        tokens = {t.strip() for t in raw.split(",") if t.strip()}
        if "*" in tokens:
            return True
        try:
            ins = specs.InstrumentSpec.of(symbol)
        except Exception:
            logger.warning("授权判定无品种行（fail-closed）: %s", symbol)
            return False
        return symbol in tokens or ins.category in tokens
    except Exception as e:   # noqa: BLE001 —— 账户规格不可读=拒（行情授权是准入面）
        logger.warning("授权判定失败（fail-closed）account=%s symbol=%s: %s", account_id, symbol, e)
        return False


def denied_symbols(account_id: int, symbols: set[str]) -> set[str]:
    return {s for s in symbols if not licensed(account_id, s)}
