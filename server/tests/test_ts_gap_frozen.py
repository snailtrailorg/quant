"""批 66c：ts 缺口检测（时段感知+段首豁免）+解冻闭环+per-market 时段门测试钉（D26 §3.4）。"""
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

from src.strategy_runner.hub_worker import _ts_gap_frozen

# 固定 +08:00（盲审 A-P2-4：datetime.now().astimezone() 取当下偏移——DST 机器季节性假红；
# 生产/测试机虽无 DST，钉住 +8 消环境依赖）
_TZ = timezone(timedelta(hours=8))


def _ep_local(y, mo, d, h, mi):
    """+08:00 epoch（缺口检测按北京钟骨架判段）。"""
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


class TestSessionDbPathRegression:
    """盲审 A/B 同判 P1 回归钉：归一不得改写 DB 查询键——market_session 表（行名 'A股'）可达。"""

    def _provider(self):
        calls = []

        def p(market_name):
            calls.append(market_name)
            if market_name == "A股":
                return {"calendar": "weekday", "session_rules": [{"open": "09:31", "close": "15:00"}]}
            return None

        p.calls = calls
        return p

    def test_registry_name_resolves_via_table_alias(self):
        """in_session("astock") 经 _TABLE_NAME_ALIASES 容错以 'A股' 命中表行（非死配置）。"""
        from src.quant_common import session as S
        p = self._provider()
        with patch.object(S, "_CONFIG_PROVIDER", p), \
             patch.object(S._load_market_config, "_cache", {}, create=True):
            S.set_config_provider(p)
            # 节假日外时段命中 DB 路径：09:00-15:00 大窗（含骨架外沿 12:00——DB 规则覆盖午休）
            from datetime import datetime as _dt
            assert S.in_session("astock", _dt(2026, 9, 28, 12, 0)) is True   # DB 大窗跨午休=真源生效
            assert "A股" in p.calls                                            # 查询键=表名
            assert "astock" not in p.calls or "A股" in p.calls                 # 归一名不得独占查询

    def test_table_name_direct_still_works(self):
        """in_session("A股") 原键直查（0053 既有路径零漂移）。"""
        from src.quant_common import session as S
        p = self._provider()
        with patch.object(S, "_CONFIG_PROVIDER", p), \
             patch.object(S._load_market_config, "_cache", {}, create=True):
            S.set_config_provider(p)
            from datetime import datetime as _dt
            assert S.in_session("A股", _dt(2026, 9, 28, 12, 0)) is True

    def test_negative_cache_stops_chatter(self):
        """负缓存：miss 后 10s 内不重查 provider（on_tick 热路径不打 DB）。"""
        from src.quant_common import session as S
        p = self._provider()   # 永远 miss（'加密永续' 未配置）
        with patch.object(S, "_CONFIG_PROVIDER", p), \
             patch.object(S._load_market_config, "_cache", {}, create=True):
            S.set_config_provider(p)
            S.in_session("crypto")
            S.in_session("crypto")
            S.in_session("crypto")
            assert p.calls.count("crypto") + p.calls.count("加密永续") <= 4   # 三次调用至多首轮双查+负缓存接管


class TestStreamMaxlenConsistency:
    def test_hub_maxlen_reads_marketspec(self):
        """B-P2-5 一致性钉：hub XADD maxlen 从 MarketSpec channels 读（改声明即生效）。"""
        from src.md_hub.main import _stream_maxlen
        from src.quant_common.markets import MARKETS
        assert _stream_maxlen("astock") == MARKETS["astock"]["channels"]["bar"]["stream_maxlen"]
        assert _stream_maxlen("crypto") == MARKETS["crypto"]["channels"]["bar"]["stream_maxlen"]
        assert _stream_maxlen("unknown") == 5000   # 缺省兜底
