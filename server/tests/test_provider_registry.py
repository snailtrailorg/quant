"""批 83b：provider 注册一致性 + 防串源 fail-fast 钉群（P0 防线 1/2）。

钉什么：
1. **三注册表一致性**（P1-1/P1-2）——adapter 注册表里每个 provider 必须有对应 DataSource，
   否则 `get_data_source` 静默回落 tushare=**串源**（失败/限速/熔断/用量全记 tushare 头上，
   熔断键 `rl:cb:tushare:0` 串源）。新增源漏注册 → 本文件红（构建期闸，补运行期 fail-fast）。
2. **fail-fast 三分域**（用户裁定"按分域分治"）——半成品集成抛 ProviderConfigError；
   完全未知返回 None 交调用方（守盲审 A-P2/B-P2）；已注册源正常实例化。
3. **engine 三层 provider 解析口径一致**（`_get_pro`/`_get_rate_ds`）——不得把
   "provider 字面量分支"写回（CI 断言一同房守门 test_contract_gate）。

注：本文件不打网络、不强依赖 DB（真库相关项走 skipif）——fail-fast 路径是**纯注册表判定**，
无库也必须成立（半成品集成的判定与 DB 无关，这正是它可靠的原因）。
"""
import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


# 声明为「已知半成品」的 adapter：只有 adapter 注册，DataSource/InterfaceProvider 未注册。
# 加新源时**不要**往这里加——正解是补齐三处注册；本表只登记历史遗留 stub。
# 本表存在即说明这些 provider 在 sync_config 里被选中会 fail-fast（而非静默串源）。
INCOMPLETE_ADAPTERS = frozenset({"joinquant", "ricequant"})


class TestThreeRegistryConsistency:
    """三注册表一致性（P1-1/P1-2：新增源须三处各注册一类，漏一处=串源）。"""

    def test_every_adapter_has_data_source_or_is_declared_incomplete(self):
        """_ADAPTERS ⊆ _REGISTRY ∪ INCOMPLETE_ADAPTERS——本批的核心构建期闸。"""
        from src.data_platform.adapters.base import _ADAPTERS
        from src.data_platform.data_source import _REGISTRY
        missing = set(_ADAPTERS) - set(_REGISTRY) - INCOMPLETE_ADAPTERS
        assert not missing, (
            f"这些 provider 注册了 adapter 但未注册 DataSource：{sorted(missing)}——"
            f"选中它们会串源（回落 tushare，用量/熔断记错账号）。请补 data_source._REGISTRY "
            f"注册，或（确属不完整 stub 时）加入本文件 INCOMPLETE_ADAPTERS 显式登记")

    def test_every_data_source_has_adapter(self):
        """_REGISTRY ⊆ _ADAPTERS——DataSource 没 adapter 就拉不到数据（反向一致性）。"""
        from src.data_platform.adapters.base import _ADAPTERS
        from src.data_platform.data_source import _REGISTRY
        assert set(_REGISTRY) <= set(_ADAPTERS), (
            f"这些 provider 注册了 DataSource 但无 adapter：{sorted(set(_REGISTRY) - set(_ADAPTERS))}")

    def test_incomplete_list_has_no_dead_entries(self):
        """半成品清单不得留死条目（防"补好了但忘删豁免"→ 闸静音）。"""
        from src.data_platform.adapters.base import _ADAPTERS
        dead = INCOMPLETE_ADAPTERS - set(_ADAPTERS)
        assert not dead, f"INCOMPLETE_ADAPTERS 死条目（该 provider 已不在 _ADAPTERS）：{sorted(dead)}"

    def test_fallback_provider_is_registered(self):
        """兜底源声明必须真的在注册表里（防声明与实际漂移）。"""
        from src.data_platform.data_source import _REGISTRY, FALLBACK_PROVIDER
        assert FALLBACK_PROVIDER in _REGISTRY


class TestFailFastNoCrossSource:
    """fail-fast 三分域（批 83b P0）：半成品集成必须响亮失败，绝不静默换源。"""

    @pytest.mark.parametrize("provider", sorted(INCOMPLETE_ADAPTERS))
    def test_incomplete_integration_raises(self, provider):
        """adapter 已注册 + DataSource 未注册 → ProviderConfigError（**不是** None）。

        这是 P0 的正身：返回 None 会让调用方 `or TushareDataSource()` 静默串源。
        """
        from src.data_platform.data_source import get_data_source
        from src.quant_common.contract import ProviderConfigError
        with pytest.raises(ProviderConfigError) as ei:
            get_data_source(provider)
        msg = str(ei.value)
        assert provider in msg
        assert "三处各注册一类" in msg          # 错误信息须给出可执行的修法

    def test_unknown_provider_returns_none(self):
        """provider 完全未知（两边都没注册）→ None，维持 A-P2/B-P2 fail-soft 域（不抛）。"""
        from src.data_platform.data_source import get_data_source
        assert get_data_source("__no_such_provider__") is None

    def test_error_is_contract_error_subclass(self):
        """继承 ContractError 家族（便于上层统一语义；但不参与 failover 决策）。"""
        from src.quant_common.contract import ContractError, ProviderConfigError
        assert issubclass(ProviderConfigError, ContractError)

    def test_registered_provider_not_affected(self):
        """已注册源（tushare）不受 fail-fast 影响——有库行给实例，无库行给 None（.env 路径）。"""
        from src.data_platform.data_source import get_data_source
        ds = get_data_source("tushare")        # 无库时返回 None 也不算错
        assert ds is None or ds.provider == "tushare"


class TestEngineProviderResolution:
    """engine 三层解析（`_provider_of` / `_get_pro` / `_get_rate_ds`）口径一致。"""

    def test_provider_of_defaults_to_fallback(self):
        from src.data_sync.engine import _provider_of
        from src.data_platform.data_source import FALLBACK_PROVIDER
        assert _provider_of({}) == FALLBACK_PROVIDER          # 缺键
        assert _provider_of({"provider": None}) == FALLBACK_PROVIDER   # 列 NULL（0067 server_default 之外的历史行）
        assert _provider_of(None) == FALLBACK_PROVIDER        # 防御
        assert _provider_of({"provider": "joinquant"}) == "joinquant"   # 真读配置

    def test_rate_ds_fails_fast_on_incomplete(self):
        """_get_rate_ds 不得吞 ProviderConfigError（吞了=限速/熔断回 tushare 串源）。"""
        from src.data_sync.engine import _get_rate_ds
        from src.quant_common.contract import ProviderConfigError
        with pytest.raises(ProviderConfigError):
            _get_rate_ds("joinquant")

    def test_rate_ds_unknown_falls_back_with_warning(self, caplog):
        """完全未知 provider → 兜底源 + 告警（A-P2/B-P2 fail-soft，不得静默）。"""
        import logging
        from src.data_sync.engine import _get_rate_ds
        from src.data_platform.data_source import FALLBACK_PROVIDER, TushareDataSource
        with caplog.at_level(logging.WARNING):
            ds = _get_rate_ds("__no_such_provider__")
        assert isinstance(ds, TushareDataSource)
        assert any("串源风险" in r.message and "__no_such_provider__" in r.getMessage()
                   for r in caplog.records), "未知 provider 回落必须告警（fail-soft 不等于静默）"

    def test_rate_ds_fallback_provider_does_not_warn(self, caplog):
        """兜底源自身缺 DB 行是合法 .env 路径——不许告警（否则每次启动都刷噪声）。"""
        import logging
        from unittest.mock import patch
        from src.data_sync.engine import _get_rate_ds
        with patch("src.data_platform.data_source.get_data_source", return_value=None), \
             caplog.at_level(logging.WARNING):
            _get_rate_ds("tushare")
        assert not any("串源风险" in r.message for r in caplog.records)

    def test_get_pro_unknown_uses_declared_fallback(self, caplog):
        """_get_pro 未知 provider → 走**声明的兜底源**（不出现 provider 字面量分支）。"""
        import logging
        from unittest.mock import MagicMock, patch
        from src.data_sync.engine import _get_pro
        fake = MagicMock()
        fake.provider = "tushare"
        with patch("src.data_platform.data_source.get_data_source", return_value=None), \
             patch("src.data_platform.data_source.fallback_data_source", return_value=fake) as fb, \
             caplog.at_level(logging.WARNING):
            _get_pro("__no_such_provider__")
        fb.assert_called_once()
        fake.record_usage.assert_called_once()      # 用量仍记账（记账对象=实际用的源）
        fake.get_client.assert_called_once()
        assert any("串源风险" in r.getMessage() for r in caplog.records)

    def test_get_pro_incomplete_integration_raises(self):
        """_get_pro 半成品集成 → 穿透 ProviderConfigError（不回落）。"""
        from src.data_sync.engine import _get_pro
        from src.quant_common.contract import ProviderConfigError
        with pytest.raises(ProviderConfigError):
            _get_pro("joinquant")
