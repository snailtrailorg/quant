"""P2-1 查询超时可观测（等待原语审计真缺陷修复，2026-08-27）。

覆盖四象限+空仓分级：
- _wait_update 有更新（预塞零线程）/超时（False+告警）
- query_position 稳定（side_effect 同步塞行零线程）/部分快照（warning）/零回报（info 不刷屏）
测试自身零固定长等待——等待类断言全部落在被测代码的轮询拍内（守则原则 1/审计教训）。
"""
import threading
from types import SimpleNamespace
from unittest.mock import MagicMock, patch


from src.strategy_framework.adapters import XTPAdapter


def _row(vt_symbol: str):
    """_positions 行对象（对齐 vnpy 推送形状，Position 消费字段见 query_position）。"""
    return SimpleNamespace(
        vt_symbol=vt_symbol, volume=100, price=9.05, pnl=1.0,
        direction=SimpleNamespace(value="long"), frozen=0, yd_volume=0,
    )


def _adapter_with(positions: dict) -> XTPAdapter:
    """绕构造实例（先例 test_position_snapshot.py）：query_position 只依赖三属性。"""
    a = XTPAdapter.__new__(XTPAdapter)
    a._lock = threading.Lock()
    a._gateway = MagicMock()
    a._positions = positions
    return a


class TestWaitUpdate:
    def test_update_arrived_true(self):
        """有更新：cache 预塞新键 → 首拍立即 True（零线程零等待）。"""
        cache = {"x": 1}
        assert XTPAdapter._wait_update(cache, before=set(), timeout=1.0) is True

    def test_timeout_false_with_warning(self, caplog):
        """超时：False + warning 含'超时'（守则原则 2——超时必须可观测）。"""
        with caplog.at_level("WARNING"):
            got = XTPAdapter._wait_update({}, before=set(), timeout=0.3)
        assert got is False
        assert "超时" in caplog.text


class TestQueryPosition:
    def test_stable_returns_rows_no_warning(self, caplog):
        """稳定：网关查询同步塞 2 行 → 两拍键集相同 → 2 行返回且无告警（零线程）。"""
        a = _adapter_with({})

        def _push_two():
            a._positions["600000.SSE"] = _row("600000.SSE")
            a._positions["000001.SZSE"] = _row("000001.SZSE")

        a._gateway.query_position.side_effect = _push_two
        with caplog.at_level("WARNING"):
            rows = a.query_position()
        assert len(rows) == 2
        assert {r.symbol for r in rows} == {"600000.SSE", "000001.SZSE"}
        assert "部分快照" not in caplog.text

    def test_partial_snapshot_warns(self, caplog):
        """部分快照：行持续变（0.05s/行=2 倍轮询拍，防同频 flake）→ 窗口耗尽 → warning。"""
        a = _adapter_with({})
        stop = threading.Event()

        def _keep_adding():
            i = 0
            while not stop.is_set() and i < 20:   # 0.05s*20=1s，足够盖过 0.4s 窗
                a._positions[f"60000{i:02d}.SSE"] = _row(f"60000{i:02d}.SSE")
                i += 1
                stop.wait(0.05)

        a._gateway.query_position.side_effect = lambda: threading.Thread(
            target=_keep_adding, daemon=True).start()
        try:
            with patch("src.strategy_framework.adapters.POSITION_STABLE_WINDOW_S", 0.4), \
                 caplog.at_level("WARNING"):
                rows = a.query_position()
        finally:
            stop.set()
        assert len(rows) >= 1                       # 部分行照返（行为不变）
        assert "部分快照" in caplog.text             # 可观测性增量

    def test_zero_rows_no_warning_noise(self, caplog):
        """零回报=空仓合法常态：info 不 warning（trading 60s 循环调用，防每分钟刷屏）。"""
        a = _adapter_with({})
        with patch("src.strategy_framework.adapters.POSITION_STABLE_WINDOW_S", 0.2), \
             caplog.at_level("WARNING"):
            rows = a.query_position()
        assert rows == []
        assert "部分快照" not in caplog.text
