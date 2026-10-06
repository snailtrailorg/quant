"""批 108·步 2：crypto 生命周期进 security_master（裁定 A / F / G①）的闸门。

设计依据：`flow/方案/同步窗口与参数分层-设计.md` §八.2 / §5.4；待裁单 A/F/G。

守门四条：
1. `upsert_rows` 签名＝**13 列**且 `session_id` 非空（缺/空 ⇒ 响亮 ValueError，不落 DB）。
2. crypto 行形状：`market='crypto'` / `session_id='crypto_247'` / `trade_phase='T+0'` /
   `vt_symbol=<源符号>.<venue>`。
3. inception **取真上币日**（OKX `listTime`）；**不可得 ⇒ None**（Binance，裁定 F），
   禁以源可达性冒充。
4. `_sm_upsert_crypto` 在 adapter 未声明 `venue` 时响亮失败（否则 SM 行全脏）。

不连网/不触库：mock 连接或 `_sm_upsert`。
"""
from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest


def _mock_conn():
    conn = MagicMock()
    conn.__enter__.return_value = conn
    conn.__exit__.return_value = False
    cur = MagicMock()
    cur.__enter__.return_value = cur
    cur.__exit__.return_value = False
    conn.cursor.return_value = cur
    return conn, cur


def _sm_client():
    from src.data_platform.security_master import SMClient
    return SMClient()


def _bare(provider: str):
    from src.data_platform.adapters.base import list_adapters
    cls = list_adapters()[provider]
    return cls.__new__(cls)


# ——— 1. upsert_rows 13 列契约 ———

class TestUpsertRowsSignature:
    def test_session_id_persisted(self):
        import src.data_platform.db as dbm
        conn, cur = _mock_conn()
        row = ("BTCUSDT.BINANCE", "crypto", "BINANCE", "perp", None, None, None,
               1, 0.01, "T+0", "2019-09-08", None, "crypto_247")
        with patch.object(dbm, "get_conn", return_value=conn):
            n = _sm_client().upsert_rows([row])
        assert n == 1
        sql, params = cur.executemany.call_args[0]
        assert "session_id" in sql and "session_id=EXCLUDED.session_id" in sql
        assert len(params[0]) == 13
        assert params[0][12] == "crypto_247"

    def test_missing_session_id_is_loud(self):
        """12 列（旧签名）⇒ 响亮 ValueError——不落 DB（否则整批 SM 行被 fail-soft 吞掉）。"""
        with pytest.raises(ValueError, match="13 列"):
            _sm_client().upsert_rows([("600000.SHSE", "astock", "SHSE", "stock", "main",
                                       "x", None, 100, 0.01, "T+1", None, None)])

    def test_empty_session_id_is_loud(self):
        with pytest.raises(ValueError, match="session_id 非空"):
            _sm_client().upsert_rows([("600000.SHSE", "astock", "SHSE", "stock", "main",
                                       "x", None, 100, 0.01, "T+1", None, None, "")])


# ——— 2. inception 源侧口（裁定 F） ———

def test_binance_inception_is_unknown():
    """Binance：`fapi` 被墙 ⇒ `onboardDate` 不可得 ⇒ **None（显式未知）**，禁近似。"""
    assert _bare("binance").symbol_inception("BTCUSDT") is None


def test_okx_inception_from_list_time():
    """OKX：`listTime`（真上币日）解析为 ISO 日期；带/不带后缀均可。"""
    inst = _bare("okx")
    ms = int(datetime(2019, 11, 12, tzinfo=timezone.utc).timestamp() * 1000)
    inst._insts = [{"instId": "BTC-USDT-SWAP", "listTime": str(ms)}]
    assert inst.symbol_inception("BTC-USDT-SWAP") == "2019-11-12"
    assert inst.symbol_inception("BTC-USDT-SWAP.OKX") == "2019-11-12"


def test_okx_inception_missing_or_dirty_is_none():
    inst = _bare("okx")
    inst._insts = [{"instId": "X-SWAP", "listTime": ""}, {"instId": "Y-SWAP", "listTime": "junk"}]
    assert inst.symbol_inception("X-SWAP") is None
    assert inst.symbol_inception("Y-SWAP") is None
    assert inst.symbol_inception("NOT-THERE") is None


# ——— 3. crypto SM 行形状（裁定 A） ———

def _capture(monkeypatch, adapter, syms):
    from src.data_sync import engine
    box: dict = {}
    monkeypatch.setattr(engine, "_sm_upsert", lambda rows: box.setdefault("rows", list(rows)))
    engine._sm_upsert_crypto(adapter, syms)
    return box["rows"]


def test_crypto_row_shape(monkeypatch):
    adapter = SimpleNamespace(provider="binance", venue="BINANCE",
                              symbol_inception=lambda s: None)
    rows = _capture(monkeypatch, adapter, ["BTCUSDT", "ETHUSDT.BINANCE"])
    assert len(rows) == 2
    r = rows[0]
    assert len(r) == 13
    assert r[0] == "BTCUSDT.BINANCE"           # 无后缀 ⇒ 拼 venue
    assert rows[1][0] == "ETHUSDT.BINANCE"     # 已带后缀 ⇒ 不重复拼
    assert (r[1], r[2], r[3]) == ("crypto", "BINANCE", "perp")
    assert r[9] == "T+0"
    assert r[10] is None                       # inception 未知 ⇒ None（显式未知）
    assert r[12] == "crypto_247"


def test_crypto_row_inception_passthrough(monkeypatch):
    """OKX：真上币日透传到 list_date 位。"""
    adapter = SimpleNamespace(provider="okx", venue="OKX",
                              symbol_inception=lambda s: "2019-11-12")
    rows = _capture(monkeypatch, adapter, ["BTC-USDT-SWAP"])
    assert rows[0][10] == "2019-11-12"
    assert rows[0][2] == "OKX" and rows[0][12] == "crypto_247"


def test_crypto_row_requires_venue(monkeypatch):
    from src.data_sync import engine
    adapter = SimpleNamespace(provider="x", venue="", symbol_inception=lambda s: None)
    with pytest.raises(RuntimeError, match="venue"):
        engine._sm_upsert_crypto(adapter, ["A"])
