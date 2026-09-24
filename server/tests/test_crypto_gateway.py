"""加密接入批测试：会话模型（24/7）+ 加密 MD 网关注册 + symbol 翻译常量。"""
from datetime import datetime

from src.md_hub.parts import _in_bar_session
from src.strategy_framework.md_gateway import (
    BinanceMdGateway, OkxMdGateway, list_md_gateway_providers,
)


class TestBarSessionMarket:
    def test_crypto_always_in_session(self):
        """加密 24/7：任意时刻（含凌晨/午休/周末）恒喂 bar。"""
        for h in (0, 3, 6, 12, 15, 18, 21, 23):
            t = datetime(2026, 9, 26, h, 30)   # 2026-09-26 周六
            assert _in_bar_session(t, "crypto") is True

    def test_astock_uses_astock_hours(self):
        assert _in_bar_session(datetime(2026, 9, 24, 10, 30), "astock") is True
        assert _in_bar_session(datetime(2026, 9, 24, 12, 0), "astock") is False    # 午休
        assert _in_bar_session(datetime(2026, 9, 24, 15, 10), "astock") is False   # 收盘后

    def test_default_market_is_astock(self):
        """缺省 market=astock（A股零回归）。"""
        assert _in_bar_session(datetime(2026, 9, 24, 10, 30)) is True


class TestCryptoGatewayRegistry:
    def test_binance_okx_registered(self):
        providers = list_md_gateway_providers()
        assert "binance_perp" in providers
        assert "okx_perp" in providers

    def test_symbol_translation_constants(self):
        """vnpy 永续 symbol 带 _SWAP_* 后缀（项目裸基 ↔ vnpy 格式双向翻译的锚）。"""
        assert BinanceMdGateway._SWAP_SUFFIX == "_SWAP_BINANCE"
        assert BinanceMdGateway._EX_VALUE == "BINANCE"
        assert OkxMdGateway._SWAP_SUFFIX == "_SWAP_OKX"
        assert OkxMdGateway._EX_VALUE == "OKX"
