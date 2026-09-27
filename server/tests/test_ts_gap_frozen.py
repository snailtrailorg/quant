"""批 66c：ts 缺口检测（时段感知+段首豁免）+解冻闭环+per-market 时段门测试钉（D26 §3.4）。"""
from datetime import datetime
from unittest.mock import patch

from src.strategy_runner.hub_worker import _ts_gap_frozen

_TZ = datetime.now().astimezone().tzinfo


def _ep_local(y, mo, d, h, mi):
    """本地时区 epoch（in_session 判定读 bar 本地钟）。"""
    return str(int(datetime(y, mo, d, h, mi, tzinfo=_TZ).timestamp()))


class TestTsGapFrozen:
    def test_intraday_gap_triggers(self):
        """盘中 60s+ 缺口 → 触发（源侧丢根）。"""
        assert _ts_gap_frozen(_ep_local(2026, 9, 28, 10, 6), _ep_local(2026, 9, 28, 10, 0), "astock") is True

    def test_lunch_break_segment_head_exempt(self):
        """午休隔段：13:01/13:02 段首豁免；13:05（真实丢根到段中）触发。"""
        assert _ts_gap_frozen(_ep_local(2026, 9, 28, 13, 1), _ep_local(2026, 9, 28, 11, 30), "astock") is False
        assert _ts_gap_frozen(_ep_local(2026, 9, 28, 13, 2), _ep_local(2026, 9, 28, 11, 30), "astock") is False
        assert _ts_gap_frozen(_ep_local(2026, 9, 28, 13, 5), _ep_local(2026, 9, 28, 11, 30), "astock") is True

    def test_overnight_not_triggered(self):
        """隔日首根（昨日尾 → 今晨 9:31/9:32）段首豁免。"""
        assert _ts_gap_frozen(_ep_local(2026, 9, 28, 9, 31), _ep_local(2026, 9, 25, 15, 0), "astock") is False
        assert _ts_gap_frozen(_ep_local(2026, 9, 28, 9, 32), _ep_local(2026, 9, 25, 15, 0), "astock") is False

    def test_outside_session_not_triggered(self):
        """盘外迟到 bar 不触发（bar 自身时刻判时段，非 wall clock）。"""
        assert _ts_gap_frozen(_ep_local(2026, 9, 28, 12, 0), _ep_local(2026, 9, 28, 9, 35), "astock") is False

    def test_crypto_24x7_gap_triggers(self):
        """crypto 24/7：60s+ 缺口=真断流即触发（无段首豁免）。"""
        assert _ts_gap_frozen(_ep_local(2026, 9, 27, 3, 6), _ep_local(2026, 9, 27, 3, 0), "crypto") is True

    def test_small_or_bad_input_not_triggered(self):
        assert _ts_gap_frozen(_ep_local(2026, 9, 28, 10, 1), _ep_local(2026, 9, 28, 10, 0), "astock") is False
        assert _ts_gap_frozen("", "123", "astock") is False
        assert _ts_gap_frozen("abc", "123", "astock") is False


class TestCryptoSessionGate:
    def test_crypto_fallback_24x7(self):
        """批 66c 修：crypto 无 market_session 配置时 fallback=24x7（原错落 A 股时段=盘外误拒 BUY）。"""
        from src.quant_common.session import in_session
        with patch("src.quant_common.session._load_market_config", return_value=None):
            assert in_session("crypto") is True
            assert in_session("astock") is in_session("A股")   # 归一双写法等价

    def test_market_of_symbol(self):
        from src.quant_common.markets import market_of_symbol
        assert market_of_symbol("BTCUSDT.BINANCE") == "crypto"
        assert market_of_symbol("600000.SHSE") == "astock"
        assert market_of_symbol("UNKNOWN.XXX") == "astock"   # 缺省


class TestRewarmUnfreezeLoop:
    def test_after_rewarm_no_retrigger(self):
        """解冻闭环（D26 §3.4②）：rewarm 推进 max_ts 过缺口后，后续 live bar 不再触发。"""
        after_rewarm = _ep_local(2026, 9, 28, 10, 6)   # 回放尾=冻结点
        assert _ts_gap_frozen(_ep_local(2026, 9, 28, 10, 7), after_rewarm, "astock") is False
