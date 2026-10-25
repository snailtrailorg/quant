# -*- coding: utf-8 -*-
"""批 147 · B 族 lag 族时点错配修——回归两钉。

margin_detail lag=1（T+1 发布）与 B 族「昨日快照」假设错配每晚误报（10-23/10-24 prod 实证）；
修=expected 对 lag 族回退一交易日。
"""
from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch

from src.data_sync import pool_reconcile as pr


def _ctx(conn):
    cm = MagicMock()
    cm.__enter__.return_value = conn
    cm.__exit__.return_value = False
    return cm


def _conn_count(n_by_table: dict[str, int]):
    """COUNT(*) 按表名返回给定值。"""
    conn = MagicMock()

    def _exec(sql, *a):
        for t, n in n_by_table.items():
            if f"FROM {t} " in sql:
                cur = MagicMock()
                cur.fetchone.return_value = (n,)
                return cur
        return MagicMock()

    conn.execute.side_effect = _exec
    return conn


class TestBLagFallback:
    """不打时间（函数内 from-import 局部绑定）——_nearest_trade_day 按输入映射：
    任意 s → 固定链 {s 若 s>=20261023 else 20261023? 不}——直接线性映射：≤ 输入的最大 20261023/20261022。

    实际语义测法：mock NTD 为「按表驱动」——expected(昨日)=E0；lag 回退一步=E1。
    以 NTD 映射表 {昨日: E0, <E0 前一日: E1} 实现两态，E0/E1 由真实昨日推出保持稳定。
    """

    def _run(self, n_by_table, e0="20261023", e1="20261022"):
        yesterday = (datetime.now() - timedelta(days=1)).strftime("%Y%m%d")
        before_e0 = (datetime.strptime(e0, "%Y%m%d") - timedelta(days=1)).strftime("%Y%m%d")

        def _ntd(s, **kw):
            s = str(s)[:8]
            if s >= e0:
                return e0
            if s >= before_e0:
                return e1
            return s   # 更早：返原值（陈旧守卫形态）

        conn = _conn_count(n_by_table)
        from src.data_sync import pool_data
        with patch.object(pr, "get_conn", return_value=_ctx(conn)), \
             patch.object(pool_data, "_nearest_trade_day", side_effect=_ntd):
            return pr.check_family_b()

    def test_lag1_expected_falls_back_one_trade_day(self):
        """margin_detail：表有 10-22 行（=回退后 expected）⇒ 无告警；st_lag 族同刻 expected=10-23。"""
        findings = self._run({"margin_detail": 5, "stk_limit": 5})
        assert findings == [], findings

    def test_lag1_gap_two_days_still_alerts(self):
        """margin_detail 表只到 10-21（缺两个交易日）⇒ 有告警且锚回退后期望日。"""
        findings = self._run({"margin_detail": 0, "stk_limit": 5})
        assert any("margin_detail" in f and "20261022" in f for f in findings), findings

    def test_lag0_family_unchanged(self):
        """lag=0 族（stk_limit）：表缺 10-23 ⇒ 告警锚=10-23（原行为零漂移）。"""
        findings = self._run({"margin_detail": 5, "stk_limit": 0})
        assert any("stk_limit" in f and "20261023" in f for f in findings), findings
        assert not any("margin_detail" in f for f in findings), findings
