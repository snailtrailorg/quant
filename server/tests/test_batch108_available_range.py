"""批 108·步 1：源可达区间硬契约（`available_range` / `publish_lag`）的 CI 闸门。

设计依据：`flow/方案/同步窗口与参数分层-设计.md` §5.1（第四边界）/ §八.1（硬契约）。

本测试**不连网、不触库**——用 `cls.__new__(cls)` 构裸实例绕过 `__init__` 副作用
（tushare 的 `__init__` 会解析数据源、okx 会建 Session），只验证契约层行为。

守门两条：
1. **覆盖完备**（双盲审① P0-2）：所有 pull-capable adapter（`capabilities` 非空）对其
   `capability_decls` 声明的**每个 kind** 都须**覆写** `available_range`（不抛）。
2. **取值正确**（`禁臆造`）：四 adapter 的**实测**取值逐条钉死，防后人改回近似值
   （`2019-09-08` / `2020-01` 这两个错值就是被本测试挡下的对象）。
"""
from __future__ import annotations

from datetime import date

import pytest

from src.data_platform.adapters.base import BaseDataAdapter, list_adapters


def _bare(provider: str) -> BaseDataAdapter:
    """构裸实例（绕过 `__init__` 的副作用），补齐契约方法真正需要的属性。"""
    cls = list_adapters()[provider]
    inst = cls.__new__(cls)
    if provider == "joinquant":
        # 裸实例无 `__init__` 设的 `_acct_win` ⇒ 手动注入假账号窗口（本测试不触网/不 auth）
        inst._acct_win = {"start": date(2020, 1, 1), "end": date(2020, 12, 31)}
    return inst


# ——— 1. 覆盖完备（CI 断言）———

def test_all_pull_capable_adapters_implement_available_range():
    """pull-capable adapter 的**每个 declared kind** 都必须给得出源可达区间。

    漏实现 ⇒ 基类默认实现抛 `NotImplementedError` ⇒ 本条红（设计 §八.1 硬契约）。
    stub adapter（`capabilities` 为空，如 ricequant）豁免：不可拉 ⇒ 无源界义务。
    """
    missing = []
    for prov, cls in list_adapters().items():
        if not getattr(cls, "capabilities", None):
            continue
        inst = _bare(prov)
        for decl in getattr(cls, "capability_decls", None) or []:
            try:
                inst.available_range(decl.kind)
            except NotImplementedError:
                missing.append(f"{prov}:{decl.kind}")
    assert not missing, f"以下 adapter/kind 未覆写 available_range（源界无真源）: {missing}"


def test_base_default_available_range_is_fail_loud():
    """基类默认实现必须 fail-loud——禁以 today / (None, None) 冒充（否则新源默认「今天起」）。"""

    class _NoImpl(BaseDataAdapter):
        provider = "noimpl"

        def pull_daily(self, *a, **k): ...
        def pull_minute(self, *a, **k): ...
        def to_bar_rows(self, *a, **k): ...

    with pytest.raises(NotImplementedError):
        _NoImpl().available_range("bar_daily")


def test_base_default_publish_lag_is_zero():
    class _NoImpl(BaseDataAdapter):
        provider = "noimpl2"

        def pull_daily(self, *a, **k): ...
        def pull_minute(self, *a, **k): ...
        def to_bar_rows(self, *a, **k): ...

    assert _NoImpl().publish_lag("bar_daily") == 0
    assert _NoImpl().publish_lag("bar_minute") == 0


# ——— 2. 取值正确（实测钉死）———

def test_binance_available_range_and_lag():
    """binance 源界＝**日包实测** `2019-12-31`（2026-10-07 探 data.binance.vision）。

    四个 interval（1d/1h/1m/15m）同值；`2019-09-08`＝上线日、`2020-01`＝月包 floor，
    **都不是**源界（用它俩 ⇒ unreachable 段永不触发 ⇒ 永久 phantom gap，双盲审② A2 P1-a）。
    """
    inst = _bare("binance")
    assert inst.available_range("bar_daily") == ("2019-12-31", None)
    assert inst.available_range("bar_minute") == ("2019-12-31", None)
    assert inst.publish_lag("bar_daily") == 1
    assert inst.publish_lag("bar_minute") == 1


def test_okx_available_range_and_lag():
    """okx 源界＝`2020-01-01`（迁移 `0137` 的 prod 实测 K 线边界；`listTime` 差 ≈2 月）。"""
    inst = _bare("okx")
    assert inst.available_range("bar_daily") == ("2020-01-01", None)
    assert inst.publish_lag("bar_daily") == 1


def test_tushare_available_range_kind_split():
    """tushare 按 kind 分级：`index_daily` 必须「无界」（否则回归），其余＝`SOURCE_EARLIEST`。"""
    inst = _bare("tushare")
    assert inst.available_range("bar_daily") == ("2010-01-01", None)
    assert inst.available_range("bar_minute") == ("2010-01-01", None)
    assert inst.available_range("index_daily") == (None, None)
    assert inst.publish_lag("bar_daily") == 0


def test_tushare_index_daily_no_regression():
    """回归守卫：index_daily 的下界不得被源界抬到 2010（须仍由 retention `2005-04-08` 决定）。

    若把 `2010-01-01` 当 index_daily 的源界 ⇒ `max(2005-04-08, 2010-01-01) = 2010`
    ⇒ 静默丢掉 2005-2009 已上线历史（本轮复核发现的**设计稿缺陷**，见任务文件 §挂账）。
    """
    src = _bare("tushare").available_range("index_daily")[0]
    retention = date(2005, 4, 8).isoformat()
    eff = max([x for x in (src, retention) if x]) if src else retention
    assert eff == retention, f"index_daily 下界被源界污染: {eff} != {retention}"


def test_joinquant_available_range_uses_account_window():
    """joinquant 源界＝账号可用窗口（第三个窗口起点站点，设计 §八.1/§九.5）。"""
    inst = _bare("joinquant")
    assert inst.available_range("bar_daily") == ("2020-01-01", "2020-12-31")
    assert inst.publish_lag("bar_daily") == 0
