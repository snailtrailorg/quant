"""批 105 · crypto partial 冻结游标 + 二轮补拉 + okx 会话连接复用。

**背景**：102b OKX 首跑 485 标的，7 个在 adapter 3 次退避耗尽后仍 `Connection reset`，
而 `_make_crypto_bar_handler` 的游标照推到「成功标的的最大数据日」⇒ 那 7 标的的**首跑
30 天窗永久缺失**（下轮增量只补 1 天）。同批 prod 实测 probe 与同步共用代理出口时
`Connection reset`（并发压力）。

验收面（全部钉「行为/契约」，不是「函数存在」）：
1. **partial 冻结（日线档）**：二轮补拉后仍有失败 ⇒ 游标退回窗口起点前一日（带
   `_PARTIAL_FREEZE_MAX_DAYS` ratchet 下限），下一轮整窗重拉自动补缺——分钟/小时档
   **不冻结**（G-S1：高频 beat × 冻结＝整窗重拉风暴），游标仍推到 reached；
2. **ratchet 下限**：窗口长于 `_PARTIAL_FREEZE_MAX_DAYS` 时冻结点卡在 `end-10d-1`，
   防「永久坏标的」把重拉窗无界推大；
3. **二轮补拉**：首轮失败标的立即重试一次，瞬时抖动自愈即清账（failed 为空、游标正常）；
4. **回补不冻结**（sync() 对回补本就不推游标）；
5. **okx 会话**：请求走 `ad._session`（连接复用），**不再**用模块级 `requests.get`；
   退避带抖动（`_backoff`），`Retry-After` 保持服务端原值不加抖动。
"""
from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest
import requests as rq

from src.data_platform.adapters import okx_adapter as OA
from src.data_sync import engine

# ---------------------------------------------------------------------------
# 工具：handler 桩（窗口用过去日期，避开「UTC 昨日」随真实时钟漂移）
# ---------------------------------------------------------------------------

_END = "20240131"          # 窗口上界（end_date 取过去 ⇒ min(end_date, UTC昨日) 恒稳定）


class _StubAdapter:
    """per-symbol 桩：`fail` 恒失败、`once_fail` 只首轮失败（二轮自愈）。"""

    provider = "stub"

    def __init__(self, symbols=("AAA", "BBB", "CCC"), fail=(), once_fail=()):
        self.symbols = list(symbols)
        self.fail = set(fail)
        self.once_fail = set(once_fail)
        self.calls: list[str] = []
        self._cur = ""

    def list_symbols(self, refresh=False):
        return list(self.symbols)

    def fetch_supply(self, kind, sub_kind=None, **params):
        self._cur = str(params["symbol"])
        self.calls.append(self._cur)
        if self._cur in self.fail:
            raise rq.exceptions.ConnectionError("Connection reset by peer")
        if self._cur in self.once_fail:
            self.once_fail.discard(self._cur)
            raise rq.exceptions.ConnectionError("transient reset")
        return pd.DataFrame([{"sym": self._cur}])

    def to_bar_rows(self, df, freq, adj_map=None):
        # ts 固定窗口末日 ⇒ reached = 20240131（成功标的的最大数据日）
        return [(self._cur, freq, datetime(2024, 1, 31, tzinfo=timezone.utc),
                 1.0, 2.0, 0.5, 1.5, 100.0, 150.0, None, self.provider)]


def _run(adapter, *, cfg_last="20240128", backfill=None, sync_id="okx_perp_daily"):
    """跑 handler：last_sync_date=20240128 ⇒ 窗口 [20240129, 20240131]（3 天）。"""
    cfg = {"id": sync_id, "provider": "stub", "last_sync_date": cfg_last}
    with patch.object(engine, "_get_supply_adapter", return_value=adapter), \
         patch("src.data_platform.db.save_bars", return_value=1) as sb, \
         patch("src.data_platform.db.save_bars_overwrite", return_value=1) as so:
        r = engine._sync_okx_perp_daily(cfg, _END, backfill)
    return r, sb, so


# ---------------------------------------------------------------------------
# 1：partial 冻结（日线档）+ ratchet 下限
# ---------------------------------------------------------------------------

class TestPartialFreeze:
    def test_partial_failure_freezes_cursor_at_window_start(self):
        """残留失败 ⇒ 游标退回窗口起点前一日（下一轮整窗重拉补缺——102b 缺窗教训的直接修复）。"""
        r, _sb, _so = _run(_StubAdapter(fail={"BBB"}))
        assert r["cursor_upto"] == "20240128", "冻结点 = start-1（窗口 [0129,0131]）"
        assert any(x.startswith("BBB:") for x in r["failed_dates"])

    def test_no_failure_keeps_101b_contract(self):
        """无失败 ⇒ 游标＝实际取到数据的最后一日（批 101b 契约不变，勿回退）。"""
        r, _sb, _so = _run(_StubAdapter())
        assert r["cursor_upto"] == "20240131" and r["failed_dates"] == []

    def test_freeze_floor_caps_window_growth(self):
        """窗口长于 `_PARTIAL_FREEZE_MAX_DAYS` ⇒ 冻结点卡 `end-10d-1`（防永久坏标的的 ratchet）。"""
        r, _sb, _so = _run(_StubAdapter(fail={"BBB"}), cfg_last="20240101")
        # 窗口 [20240102, 20240131]（30 天）；end-10d = 0121 ⇒ 冻结点 0120
        assert r["cursor_upto"] == "20240120"

    def test_minute_tier_still_advances(self):
        """分钟/小时档（freeze_on_partial=False）失败**仍无条件推进**——G-S1 防 beat 风暴。"""
        ad = _StubAdapter(fail={"BBB"})
        cfg = {"id": "binance_perp_1min", "provider": "binance", "last_sync_date": "20240128"}
        with patch.object(engine, "_get_supply_adapter", return_value=ad), \
             patch("src.data_platform.db.save_bars", return_value=1):
            r = engine._sync_binance_perp_1min(cfg, _END, None)
        assert r["cursor_upto"] == "20240131", "高频档冻结＝重拉风暴，宁可洞靠告警暴露"
        assert any(x.startswith("BBB:") for x in r["failed_dates"])

    def test_backfill_never_freezes(self):
        """回补不冻结（sync() 对回补本就不推游标；冻结语义只对增量游标有意义）。"""
        r, _sb, _so = _run(_StubAdapter(fail={"BBB"}), backfill="20240101")
        assert r["cursor_upto"] == "20240131"

    def test_second_pass_heals_transient(self):
        """首轮失败标的**立即重试一次**：瞬时抖动自愈即清账（failed 空、游标正常推进）。"""
        ad = _StubAdapter(once_fail={"BBB"})
        r, _sb, _so = _run(ad)
        assert r["failed_dates"] == []
        assert r["cursor_upto"] == "20240131"
        assert ad.calls.count("BBB") == 2, "首轮 + 二轮补拉各一次"

    def test_second_pass_failure_still_recorded(self):
        """二轮仍失败 ⇒ 记账不丢（错误取最后一轮），冻结语义照常生效。"""
        ad = _StubAdapter(fail={"BBB"})
        r, _sb, _so = _run(ad)
        assert sum(1 for c in ad.calls if c == "BBB") == 2, "恒失败标的也过了二轮"
        assert len(r["failed_dates"]) == 1 and r["failed_dates"][0].startswith("BBB:")
        assert r["cursor_upto"] == "20240128"

    def test_all_failed_freezes_and_reports(self):
        """全市场失败：游标冻结、failed 全记（批 104 终态词=failed 的输入侧）。"""
        r, _sb, _so = _run(_StubAdapter(fail={"AAA", "BBB", "CCC"}))
        assert r["pulled"] == 0 and len(r["failed_dates"]) == 3
        assert r["cursor_upto"] == "20240128"

    def test_failed_list_is_sorted(self):
        """失败清单排序输出（多标的失败时 sync_log/告警内容确定性）。"""
        r, _sb, _so = _run(_StubAdapter(fail={"CCC", "AAA"}))
        got = [x.split(":")[0] for x in r["failed_dates"]]
        assert got == sorted(got) == ["AAA", "CCC"]


# ---------------------------------------------------------------------------
# 2：okx 会话（连接复用）+ 抖动退避
# ---------------------------------------------------------------------------

def _resp(status=200, body=None, headers=None):
    r = MagicMock()
    r.status_code = status
    r.json.return_value = body if body is not None else {"code": "0", "data": []}
    r.headers = headers or {}
    return r


class TestAdapterSession:
    def test_requests_go_through_session_not_module_get(self):
        """请求必须走 `ad._session`（连接复用）——模块级 `requests.get` 每请求新建连接，
        485 标的 × 多页＝数千次握手压同一代理出口（102b `Connection reset` 第一嫌疑）。"""
        ad = OA.OkxAdapter()
        with patch.object(OA.requests, "get",
                          side_effect=AssertionError("禁止模块级 requests.get")):
            with patch.object(ad._session, "get", return_value=_resp()) as g:
                assert ad._request("/x", {})["code"] == "0"
        assert g.call_count == 1

    def test_session_is_persistent_per_adapter(self):
        """会话挂实例上：同一 adapter 的多次请求复用同一 Session（任务生命周期内）。"""
        ad = OA.OkxAdapter()
        with patch.object(ad._session, "get", return_value=_resp()) as g:
            ad._request("/x", {})
            ad._request("/y", {})
        assert g.call_count == 2

    def test_proxies_reach_session_get(self):
        """代理经出口注入仍落到每次请求（会话化不得丢失 102a 的出口语义）。"""
        ad = OA.OkxAdapter()
        ad.configure_exit(proxy="socks5h://h:1080")
        with patch.object(ad._session, "get", return_value=_resp()) as g:
            ad._request("/x", {})
        assert g.call_args.kwargs["proxies"] == {"http": "socks5h://h:1080",
                                                 "https": "socks5h://h:1080"}

    def test_backoff_has_jitter_bounds(self):
        """`_backoff` = 指数退避 × (1 + U(0, jitter))；抖动把重试波峰打散（批 105 动机）。"""
        with patch.object(OA.random, "uniform", return_value=0.3) as u:
            assert OA._backoff(0) == pytest.approx(2.0 * 1.3)
            assert OA._backoff(2) == pytest.approx(8.0 * 1.3)
        assert u.call_args.args == (0, OA._RETRY_JITTER)

    def test_retry_after_stays_raw(self):
        """`Retry-After` 由服务端给出，**不加抖动**（尊重上游指示；102b 既有断言语义）。"""
        ad = OA.OkxAdapter()
        seq = [_resp(status=429, headers={"Retry-After": "7"}),
               _resp(body={"code": "0", "data": []})]
        with patch.object(ad._session, "get", side_effect=seq), \
             patch.object(OA.time, "sleep") as sl:
            assert ad._request("/x", {})["code"] == "0"
        assert sl.call_args.args[0] == 7.0
