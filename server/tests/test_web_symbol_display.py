"""批175f 守门：前端**展示**标的必须带交易所后缀（`600000.SHSE`）。

背景：批175（标的主标识归一）把入站/落库/读侧都归一到 vt，但漏了"显示层"——
`Trading.vue`/`Dashboard.vue` 用 `(row.symbol||'').split('.')[0]` 把后缀剥掉，
用户看到的持仓/委托只有裸码。本守门把该习惯钉死（含回潮形态）。

不覆盖（非展示，允许）：
- 选股页 `_key = (s) => (s||'').split('.')[0]`：行匹配键，不上屏；
- `.SZ/.SH → .SZSE/.SHSE` 的**入参**转换（LiveTask/PaperTrade 的 `EX` 表）。
"""
from pathlib import Path

import pytest

_WEB_SRC = Path(__file__).resolve().parents[2] / "web" / "src"
_SUFFIXES = (".vue", ".js", ".ts")

# 禁用形态：symbol 变量在 30 字符内被 split('.') 取第 0 段（＝剥交易所后缀）
_FORBIDDEN = ("symbol", ".split('.'", '[0]')


def _hits() -> list[tuple[str, int, str]]:
    out: list[tuple[str, int, str]] = []
    for p in _WEB_SRC.rglob("*"):
        if p.suffix not in _SUFFIXES:
            continue
        for i, line in enumerate(p.read_text(encoding="utf-8").splitlines(), 1):
            stripped = line.strip()
            if stripped.startswith("//") or stripped.startswith("*"):
                continue
            if all(tok in line for tok in _FORBIDDEN) or 'split(".")[0]' in line and "symbol" in line:
                out.append((str(p.relative_to(_WEB_SRC)), i, stripped[:110]))
    return out


def test_no_symbol_suffix_stripping_in_web():
    """前端任何位置都不得把 symbol 的后缀 split 掉（展示口径＝后端 canonical）。"""
    hits = _hits()
    assert not hits, "发现前端剥标的后缀（应直接展示 symbol）：\n" + "\n".join(
        f"  {f}:{ln}  {txt}" for f, ln, txt in hits)


def test_fmtcode_helper_removed():
    """`fmtCode` 是"剥后缀上屏"的旧口径封装，已删除——回潮即红。"""
    js = (_WEB_SRC / "utils" / "format.js").read_text(encoding="utf-8")
    assert "export const fmtCode" not in js


def test_screen_rows_expose_vt_symbol():
    """批175f：选股行必须带 vt_symbol（展示口径），原只回 ts_code（600000.SH）。"""
    from unittest.mock import MagicMock, patch
    from src.data_platform import screen as SC

    conn = MagicMock()
    conn.__enter__.return_value = conn
    cur = MagicMock()
    cur.description = [("ts_code",), ("name",)]
    cur.fetchall.return_value = [("601939.SH", "建设银行"), ("920085.BJ", "北交所样例")]
    conn.execute.return_value = cur
    with patch("src.data_platform.db.get_conn", return_value=conn), \
         patch.object(SC, "build_base_from", return_value="SELECT 1 AS ts_code"):
        out = SC.run_query("stock", [], "", [], 10, [])
    syms = [r["vt_symbol"] for r in out["rows"]]
    assert syms == ["601939.SHSE", "920085.BSE"]
    assert out["rows"][0]["ts_code"] == "601939.SH"      # 源口径保留（join 用）


# ——— 选股列表三端点（stock.py 手写 SQL 路径）也必须回 vt_symbol ———
def _fake_conn(rows):
    from unittest.mock import MagicMock
    conn = MagicMock()
    conn.__enter__.return_value = conn
    cur = MagicMock()
    cur.fetchall.return_value = rows
    conn.execute.return_value = cur
    return conn


def test_screen_astock_api_exposes_vt_symbol():
    from unittest.mock import patch
    from src.web_api.routes import stock as R
    row = ("601939.SH", "建设银行", 12.1, 9.3, 8.9, 0.86, 1.3, 3.1e8, 1.1e8, 4.8, 2.0, None, None)
    with patch.object(R, "get_conn", return_value=_fake_conn([row])):
        out = R.screen_astock_api(payload={})
    assert out[0]["vt_symbol"] == "601939.SHSE" and out[0]["ts_code"] == "601939.SH"


def test_screen_cb_api_exposes_vt_symbol():
    from unittest.mock import patch
    from src.web_api.routes import stock as R
    row = ("113056.SH", "重银转债", "601963.SH", "重庆银行", 9.22, "20280323",
           123.4, 12.1, 130.0, 5.0)
    with patch.object(R, "get_conn", return_value=_fake_conn([row])):
        out = R.screen_cb_api(payload={})
    assert out[0]["vt_symbol"] == "113056.SHSE"
    assert out[0]["stk_vt_symbol"] == "601963.SHSE"


def test_screen_etf_api_exposes_vt_symbol():
    from unittest.mock import patch
    from src.web_api.routes import stock as R
    row = ("158907.SZ", "国泰鑫汇", "国泰基金", "股票型", None, 12.0, 0.5, 1.0)
    with patch.object(R, "get_conn", return_value=_fake_conn([row])):
        out = R.screen_etf_api(payload={})
    assert out[0]["vt_symbol"] == "158907.SZSE"
