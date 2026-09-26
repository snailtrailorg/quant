"""批 61·M6 账号切换状态机测试（A04 §九验收判据：状态机全路径/检查单拒切/L1 预检拒/事务序列）。

mock 姿势（任务文件）：_FakeConn 按 SQL 关键字分发注入（test_routing 模板）——无库可跑；
staging 行为级演练另挂（验收⑥）。
"""
import os
import json
from datetime import datetime, timedelta, timezone
import pytest
from unittest.mock import MagicMock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from src.data_platform import trade_switch as ts

_NOW = datetime.now(timezone.utc)


class _Cur(MagicMock):
    def __init__(self, rows=None, cols=None, rowcount=0):
        super().__init__()
        self._rows = rows or []
        self.description = [(c,) for c in cols] if cols else []
        self.rowcount = rowcount

    def fetchone(self):
        return self._rows[0] if self._rows else None

    def fetchall(self):
        return list(self._rows)


class _FakeConn:
    """trade_switch 引擎全 SQL 形态分发（构造注入行；捕获写入断言状态转移序列）。

    session=dict(id/state/from/to/nominated_at/extended_at/past_deadline)——按各查询实际
    列序现场拼行（confirm=SQL 时限谓词 UPDATE→0 行归因 SELECT state；execute=FOR UPDATE）。
    """

    def __init__(self, *, accounts=None, timeout_s=300, active_session=None,
                 session=None, open_orders=None, snapshot=None, trades=None,
                 tasks=None, running_n=0, expired_flip=None, switched_rowcount=2,
                 referenced_n=None):
        self.accounts = accounts or {}          # id -> (id,name,provider,market,caps,enabled)
        self.timeout_s = timeout_s
        self.active_session = active_session    # nominate 活跃检查注入行
        self.session = session or {}            # FOR UPDATE 会话行（dict，见类注）
        self.open_orders = open_orders or []
        self.snapshot = snapshot                # (ts, total_value) or None
        self.tasks = tasks or []                # affected_tasks 注入
        self.running_n = running_n
        self.expired_flip = expired_flip        # lazy expire RETURNING 行
        self.switched_rowcount = switched_rowcount
        self.referenced_n = len(self.tasks) if referenced_n is None else referenced_n
        self.executed = []                      # (sql, params) 捕获
        self.commits = 0

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def commit(self):
        self.commits += 1

    def execute(self, sql, params=None):
        self.executed.append((sql, params))
        s = sql.strip()
        if "FROM routing_policy" in s:
            return _Cur(rows=[(self.timeout_s,)], cols=["trade_switch_confirm_timeout_s"])
        if "FROM external_interface WHERE id" in s:
            r = self.accounts.get(params[0] if params else None)
            return _Cur(rows=[r] if r else [], cols=["id", "name", "provider", "market",
                                                     "capabilities", "enabled"])
        if "FROM order_log" in s:
            return _Cur(rows=self.open_orders,
                        cols=["client_order_id", "symbol", "status", "ts"])
        if "daily_pnl, available_cash FROM account_snapshot" in s:
            return _Cur(rows=[self.snapshot] if self.snapshot else [],
                        cols=["ts", "total_value", "daily_pnl", "available_cash"])
        if "total_value FROM account_snapshot" in s:
            return _Cur(rows=[self.snapshot] if self.snapshot else [],
                        cols=["ts", "total_value"])
        if "SELECT id, name, status FROM live_task" in s:
            return _Cur(rows=self.tasks, cols=["id", "name", "status"])
        if "FROM trade_switch_session WHERE to_account" in s:
            return _Cur(rows=[(self.active_session,)] if self.active_session else [],
                        cols=["id"])
        if "INSERT INTO trade_switch_session" in s:
            return _Cur(rows=[(101, _NOW)], cols=["id", "nominated_at"])
        if "SET state='expired'" in s and "RETURNING id, to_account" in s:
            return _Cur(rows=[(i, 9) for i in (self.expired_flip or [])],
                        cols=["id", "to_account"])
        if "FOR UPDATE" in s and "FROM trade_switch_session" in s:
            if not self.session:
                return _Cur(rows=[], cols=["id"])
            if "extended_at" in s:   # confirm 的 SELECT id,state,extended_at,nominated_at
                return _Cur(rows=[(self.session["id"], self.session["state"],
                                   self.session.get("extended_at"),
                                   self.session.get("nominated_at", _NOW))],
                            cols=["id", "state", "extended_at", "nominated_at"])
            return _Cur(rows=[(self.session["id"], self.session["state"],
                               self.session.get("from", 1), self.session.get("to", 2))],
                        cols=["id", "state", "from_account", "to_account"])
        if "SELECT id FROM live_task WHERE account_id=%s FOR UPDATE" in s:
            return _Cur(rows=[(t[0],) for t in self.tasks], cols=["id"])
        if "SELECT count(*) FROM live_task" in s:
            if "status='running'" in s:
                return _Cur(rows=[(self.running_n,)], cols=["count"])
            return _Cur(rows=[(self.referenced_n,)], cols=["count"])   # ⑤ from 引用计数
        if "SET state='confirmed'" in s:
            # confirm 的 SQL 时限谓词：nominated 且未过限才命中（past_deadline 模拟 DB 时钟）
            ok = (self.session.get("state") == "nominated"
                  and not self.session.get("past_deadline"))
            return _Cur(rows=[(self.session.get("id"),)] if ok else [], cols=["id"])
        if "SELECT state FROM trade_switch_session WHERE id" in s:
            return _Cur(rows=[(self.session.get("state"),)] if self.session else [],
                        cols=["state"])
        if "UPDATE live_task SET account_id" in s:
            return _Cur(rowcount=self.switched_rowcount)
        if "extended_at=now()" in s:
            ok = (self.session.get("state") == "nominated"
                  and not self.session.get("extended_at")
                  and not self.session.get("past_deadline"))   # SQL 时限谓词模拟
            return _Cur(rows=[(self.session.get("id"),)] if ok else [],
                        cols=["id"])
        if "SET state='aborted'" in s:
            ok_states = ("nominated", "confirmed")   # 模拟 WHERE state IN (...) 条件
            rows = ([(self.session.get("id"), "aborted")]
                    if self.session.get("state") in ok_states else [])
            return _Cur(rows=rows,
                        cols=["id", "state"])
        if "FROM trade_switch_session" in s and "ORDER BY id DESC" in s:
            return _Cur(rows=[], cols=["id"])
        # UPDATE trade_switch_session 状态转移/checklist 合并等
        return _Cur()


def _acct(aid, market="astock", caps=("trading", "rt_quote"), enabled=True):
    return (aid, f"acct-{aid}", "xtp", market, list(caps), enabled)


# --- 提名 ---

def test_nominate_ok_and_info_pack_shape():
    conn = _FakeConn(accounts={1: _acct(1), 2: _acct(2)},
                     open_orders=[("c1", "600000.SH", "submitted", _NOW)],
                     snapshot=(_NOW, 123456.7, 10.0, 5000.0),
                     tasks=[(7, "task-a", "stopped")])
    with patch.object(ts, "get_conn", return_value=conn):
        out = ts.nominate_switch(1, 2, "alice", cred_ok=True)
    assert out["id"] == 101 and out["timeout_s"] == 300
    ip = json.loads([p for s, p in conn.executed
                     if "INSERT INTO trade_switch_session" in s][0][2])
    assert ip["open_orders"][0]["client_order_id"] == "c1"
    assert ip["open_orders_caveat"] and "撤单" in ip["open_orders_caveat"]
    assert ip["to_account_state"]["available"] is True
    assert ip["affected_tasks"] == [{"id": 7, "name": "task-a", "status": "stopped"}]
    assert ip["perms_reeval"]["to_market"] == "astock"
    cl = json.loads([p for s, p in conn.executed
                     if "INSERT INTO trade_switch_session" in s][0][3])
    assert cl["cred_complete"] and cl["manual_verified"] is False


def test_nominate_rejections():
    def mk(**kw):
        d = dict(accounts={1: _acct(1), 2: _acct(2)})
        d.update(kw)
        return _FakeConn(**d)

    cases = [
        (mk(), 1, 1, "相同"),
        (mk(accounts={1: _acct(1, market="crypto"), 2: _acct(2)}), 1, 2, "跨市场"),
        (mk(accounts={1: _acct(1), 2: _acct(2, enabled=False)}), 1, 2, "未启用"),
        (mk(accounts={1: _acct(1), 2: _acct(2, caps=("rt_quote",))}), 1, 2, "trading 能力"),
        (mk(active_session=55), 1, 2, "活跃会话"),
        (mk(accounts={1: _acct(1)}), 1, 99, "不存在"),
    ]
    for conn, f, t, why in cases:
        with patch.object(ts, "get_conn", return_value=conn):
            try:
                ts.nominate_switch(f, t, "alice", cred_ok=True)
                raise AssertionError(f"应拒：{why}")
            except ValueError:
                pass
        assert not any("INSERT INTO trade_switch_session" in s for s, _ in conn.executed), why


# --- 确认/超时/续时 ---

def test_confirm_ok():
    conn = _FakeConn(session={"id": 101, "state": "nominated", "extended_at": None,
                           "nominated_at": _NOW, "from": 1, "to": 2},
                  snapshot=(_NOW, 1.0))
    with patch.object(ts, "get_conn", return_value=conn):
        out = ts.confirm_switch(101, "alice")
    assert out["state"] == "confirmed"
    assert any("state='confirmed', confirmed_at=now()" in s for s, _ in conn.executed)


def test_confirm_expired_on_deadline():
    # 时限判定在 DB 侧 SQL 谓词（与 lazy expire 同源时钟）——past_deadline 模拟 DB 判限
    conn = _FakeConn(session={"id": 101, "state": "nominated", "extended_at": None,
                           "nominated_at": _NOW - timedelta(seconds=400), "from": 1, "to": 2,
                           "past_deadline": True},
                  timeout_s=300)
    with patch.object(ts, "get_conn", return_value=conn):
        out = ts.confirm_switch(101, "alice")
    assert out["state"] == "expired" and "时限" in out["reason"]
    assert any("SET state='expired'" in s for s, _ in conn.executed)


def test_extend_once_only():
    conn = _FakeConn(session={"id": 101, "state": "nominated", "extended_at": None,
                           "nominated_at": _NOW, "from": 1, "to": 2})
    with patch.object(ts, "get_conn", return_value=conn):
        assert ts.extend_session(101, "alice")["extended"] is True
    # 二次续时：UPDATE 无 RETURNING 行（extended_at IS NULL 不满足）
    conn2 = _FakeConn(session={})
    with patch.object(ts, "get_conn", return_value=conn2):
        try:
            ts.extend_session(101, "alice")
            raise AssertionError("二次续时应拒")
        except ValueError:
            pass


def test_confirm_rejects_non_nominated():
    for state in ("confirmed", "done", "expired", "aborted"):
        conn = _FakeConn(session={"id": 101, "state": state, "extended_at": None,
                         "nominated_at": _NOW, "from": 1, "to": 2})
        with patch.object(ts, "get_conn", return_value=conn):
            try:
                ts.confirm_switch(101, "alice")
                raise AssertionError(f"{state} 不可确认")
            except ValueError:
                pass


# --- 执行：检查单四必过 ---

def _exec_env(**kw):
    defaults = dict(session={"id": 101, "state": "confirmed", "extended_at": None,
                         "nominated_at": _NOW, "from": 1, "to": 2},
                accounts={1: _acct(1), 2: _acct(2)},
                    snapshot=(_NOW, 999.0), tasks=[(7, "t", "stopped")], running_n=0)
    defaults.update(kw)
    return _FakeConn(**defaults)


def test_execute_success_atomic_sequence():
    conn = _exec_env()
    with patch.object(ts, "get_conn", return_value=conn):
        out = ts.execute_switch(101, "alice", manual_verified=True, cred_ok=True)
    assert out["executed"] is True and out["switched_tasks"] == 2
    assert out["anchor"]["total_value"] == 999.0
    seq = [s for s, _ in conn.executed]
    i_lock_live = seq.index([s for s in seq if "SELECT id FROM live_task WHERE account_id=%s FOR UPDATE" in s][0])
    i_switch = seq.index([s for s in seq if "UPDATE live_task SET account_id" in s][0])
    i_done = seq.index([s for s in seq if "state='done'" in s][0])
    assert i_lock_live < i_switch < i_done   # 事务序列：锁→切换→done
    assert conn.commits >= 1


def test_execute_blocked_open_orders():
    conn = _exec_env(open_orders=[("c9", "000001.SZ", "submitted", _NOW)])
    with patch.object(ts, "get_conn", return_value=conn):
        out = ts.execute_switch(101, "alice", manual_verified=True, cred_ok=True)
    assert out["executed"] is False and "open_orders_clear" in out["recheck"]["failed"]
    assert not any("UPDATE live_task SET account_id" in s for s, _ in conn.executed)


def test_execute_blocked_missing_manual_verification():
    conn = _exec_env()
    with patch.object(ts, "get_conn", return_value=conn):
        out = ts.execute_switch(101, "alice", manual_verified=False, cred_ok=True)
    assert out["executed"] is False and "open_orders_clear" in out["recheck"]["failed"]


def test_execute_blocked_cred_or_account():
    conn = _exec_env()
    with patch.object(ts, "get_conn", return_value=conn):
        out = ts.execute_switch(101, "alice", manual_verified=True, cred_ok=False)
    assert out["executed"] is False and "to_account_l1" in out["recheck"]["failed"]
    conn2 = _exec_env(accounts={1: _acct(1), 2: _acct(2, enabled=False)})
    with patch.object(ts, "get_conn", return_value=conn2):
        out2 = ts.execute_switch(101, "alice", True, True)
    assert out2["executed"] is False and "to_account_l1" in out2["recheck"]["failed"]


def test_execute_blocked_no_snapshot_anchor():
    conn = _exec_env(snapshot=None)
    with patch.object(ts, "get_conn", return_value=conn):
        out = ts.execute_switch(101, "alice", manual_verified=True, cred_ok=True)
    assert out["executed"] is False and "anchor_snapshot" in out["recheck"]["failed"]


def test_execute_blocked_running_tasks():
    conn = _exec_env(running_n=1)
    with patch.object(ts, "get_conn", return_value=conn):
        out = ts.execute_switch(101, "alice", manual_verified=True, cred_ok=True)
    assert out["executed"] is False and "no_running_tasks" in out["recheck"]["failed"]


def test_execute_rejects_non_confirmed():
    for state in ("nominated", "done", "expired", "aborted"):
        conn = _FakeConn(session={"id": 101, "state": state, "extended_at": None,
                         "nominated_at": _NOW, "from": 1, "to": 2})
        with patch.object(ts, "get_conn", return_value=conn):
            try:
                ts.execute_switch(101, "alice", True, True)
                raise AssertionError(f"{state} 不可执行")
            except ValueError:
                pass


# --- 中止 ---

def test_abort_two_states_and_reason_in_checklist():
    for state in ("nominated", "confirmed"):
        conn = _FakeConn(session={"id": 101, "state": state, "extended_at": None,
                         "nominated_at": _NOW, "from": 1, "to": 2})
        with patch.object(ts, "get_conn", return_value=conn):
            out = ts.abort_switch(101, "alice", "策略调整")
        assert out["state"] == "aborted"
        p = [p for s, p in conn.executed if "SET state='aborted'" in s][0]
        assert json.loads(p[0])["abort_reason"] == "策略调整"
    conn = _FakeConn(session={"id": 101, "state": "done", "extended_at": None,
                         "nominated_at": _NOW, "from": 1, "to": 2})
    with patch.object(ts, "get_conn", return_value=conn):
        try:
            ts.abort_switch(101, "alice", "x")
            raise AssertionError("终态不可中止")
        except ValueError:
            pass


# --- lazy expire ---

def test_lazy_expire_only_nominated_and_flips_reported():
    conn = _FakeConn(expired_flip=[301, 302])
    with patch.object(ts, "get_conn", return_value=conn):
        out = ts.list_sessions()
    assert out["expired_flipped"] == 2
    s = [s for s, _ in conn.executed if "SET state='expired'" in s][0]
    assert "coalesce(extended_at, nominated_at)" in s and "make_interval" in s


# --- 代码审补钉（A-P1-4/B-P1-2 同判裁定 + A-P1-2/A-P1-6/B-P2-3） ---

def test_nominate_rejects_l1_cred_fail():
    """L1 凭证不完整即拒（A04 立法②「死账号挡在提名前」——不建会话不告警照旧跑）。"""
    conn = _FakeConn(accounts={1: _acct(1), 2: _acct(2)})
    with patch.object(ts, "get_conn", return_value=conn):
        try:
            ts.nominate_switch(1, 2, "alice", cred_ok=False)
            raise AssertionError("L1 凭证不过应拒")
        except ValueError as e:
            assert "凭证" in str(e)
    assert not any("INSERT INTO trade_switch_session" in s for s, _ in conn.executed)


def test_execute_blocked_from_not_referenced():
    """第五判定：from 无 live_task 引用（链式会话后执行者）→ 拒切防假成功零效果。"""
    conn = _exec_env(referenced_n=0)
    with patch.object(ts, "get_conn", return_value=conn):
        out = ts.execute_switch(101, "alice", manual_verified=True, cred_ok=True)
    assert out["executed"] is False and "from_still_referenced" in out["recheck"]["failed"]


def test_confirmed_has_no_deadline():
    """超时语义钉：时限只辖 nominated——confirmed 过限仍可 execute（无时限谓词拦截）。"""
    conn = _exec_env()   # confirmed 会话；execute 路径不含任何时限检查
    with patch.object(ts, "get_conn", return_value=conn):
        out = ts.execute_switch(101, "alice", manual_verified=True, cred_ok=True)
    assert out["executed"] is True
    assert not any("make_interval" in s for s, _ in conn.executed), "execute 不应含时限谓词"


def test_lazy_expire_predicate_nominated_only():
    """lazy expire SQL 谓词钉：state='nominated'（confirmed 会话不被过期翻转）。"""
    conn = _FakeConn(expired_flip=[])
    with patch.object(ts, "get_conn", return_value=conn):
        ts.list_sessions()
    s = [s for s, _ in conn.executed if "SET state='expired'" in s][0]
    assert "state='nominated'" in s


def test_extend_rejected_past_deadline():
    """续时须在时限内（防复活已超时提名——A-P2-5）。"""
    conn = _FakeConn(session={"id": 101, "state": "nominated", "extended_at": None,
                              "nominated_at": _NOW, "from": 1, "to": 2, "past_deadline": True})
    with patch.object(ts, "get_conn", return_value=conn):
        try:
            ts.extend_session(101, "alice")
            raise AssertionError("超时后不可续时")
        except ValueError:
            pass
    s = [s for s, _ in conn.executed if "extended_at=now()" in s][0]
    assert "make_interval" in s and ">= now()" in s   # SQL 时限谓词在位


# --- P0 回归钉：_OPEN_SQL 对真库行为级（双盲 A/B 同判要求——mock 关键字分发对列名零保真） ---

def test_open_sql_real_db_roundtrip():
    """真库行为级：假 order_log 在途行被查出；补 trade_log 关联成交行后被排除。

    dev 库不可达时 skip（CI 环境）；关联键 t.order_id=o.id（修复前 t.client_order_id
    列不存在+bigint=text 双炸——此钉防再犯）。
    """
    from src.data_platform.db import get_conn
    try:
        with get_conn() as conn:
            aid = conn.execute("SELECT id FROM external_interface LIMIT 1").fetchone()
            if not aid:
                pytest.skip("dev 库无 external_interface 行")
            aid = aid[0]
            conn.execute("BEGIN")
            cur = conn.execute(
                "INSERT INTO order_log (symbol, action, price, volume, status, ts, account_id) "
                "VALUES ('600000.SH', 'buy', 10.0, 100, 'submitted', now(), %s) RETURNING id",
                (aid,))
            oid = cur.fetchone()[0]
            try:
                rows = conn.execute(ts._OPEN_SQL, (aid,)).fetchall()
                n_before = len(rows)
                # 关联成交行 → 被排除
                conn.execute(
                    "INSERT INTO trade_log (ts, symbol, action, volume, price, order_id, account_id) "
                    "VALUES (now(), '600000.SH', 'buy', 100, 10.0, %s, %s)", (oid, aid))
                rows2 = conn.execute(ts._OPEN_SQL, (aid,)).fetchall()
                assert len(rows2) == n_before - 1, f"关联成交后应排除（{n_before} → {len(rows2)}）"
            finally:
                conn.execute("ROLLBACK")   # 自建自清：测试行不落库
    except Exception as e:
        if "connection" in str(e).lower() or "refused" in str(e).lower():
            pytest.skip(f"dev 库不可达: {e}")
        raise
