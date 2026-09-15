"""批27-1：邮件发件箱 sending 死行回收测试。

场景：进程死在 SMTP 发送窗（claim commit 后/终态写库前）→ 行停在 sending——
sweep 按 claim 锚（now()+10min）过期回收；attempts+1 防无限重发；达上限直接 failed。"""
from unittest.mock import MagicMock, patch


def _conn(scripted=None):
    """scripted conn：记录全部 execute(sql, params) 供断言；fetchone/fetchall 按序返回
    （scripted 元素若 list→fetchall，否则→fetchone）。"""
    conn = MagicMock(); conn.__enter__.return_value = conn
    calls = []
    state = {"n": 0}

    def execute(sql, *a):
        calls.append((sql, a))
        cur = MagicMock()
        if scripted and state["n"] < len(scripted):
            s = scripted[state["n"]]
            cur.fetchall.return_value = s if isinstance(s, list) else []
            cur.fetchone.return_value = None if isinstance(s, list) else s
        else:
            cur.fetchone.return_value = None
            cur.fetchall.return_value = []
        state["n"] += 1
        return cur
    conn.execute.side_effect = execute
    conn._calls = calls
    return conn


def _sql_contains(conn, fragment):
    return any(fragment in c[0] for c in conn._calls)


class TestSweepRecovery:
    def test_stale_sending_recovered_with_attempts(self):
        """sending 过期行回收：status 回 pending、attempts+1（防已发成功写库前死→无限重发）。"""
        import src.email_service as E
        conn = _conn(scripted=[None, [], ])   # 回收 UPDATE（无 fetchone）→ SELECT ids 空
        with patch("src.data_platform.db.get_conn", return_value=conn), \
             patch.object(E, "_try_row_sync") as trs:
            E.sweep(limit=3)
        assert _sql_contains(conn, "WHERE status='sending' AND next_attempt_at<=now()"), "sweep 应先回收 sending 死行"
        assert _sql_contains(conn, "attempts=attempts+1"), "回收必须计次"
        assert any("commit" in dir(conn) for _ in [0])
        conn.commit.assert_called(), "回收 UPDATE 必须显式 commit（with 退出=回滚）"
        trs.assert_not_called()   # ids 空——不发

    def test_recovered_row_eligible_for_immediate_retry(self):
        """回收后 next_attempt_at 仍为过期锚 → 同轮 SELECT pending 即捞起重发（设计意图）。"""
        import src.email_service as E
        conn = _conn(scripted=[None, [(7,)]])   # 回收 → SELECT 捞到 id=7
        with patch("src.data_platform.db.get_conn", return_value=conn), \
             patch.object(E, "_try_row_sync") as trs:
            E.sweep(limit=3)
        trs.assert_called_once_with(7)

    def test_claim_writes_lease_anchor(self):
        """claim 段写认领锚 now()+10min（回收依据）。"""
        import src.email_service as E
        conn = _conn(scripted=[(1, "a@x", "s", "b", 0)])   # claim RETURNING 行
        with patch("src.data_platform.db.get_conn", return_value=conn), \
             patch.object(E, "_send_email_sync", return_value=None), \
             patch("src.data_platform.log_sink.event"), \
             patch.object(E, "_final_failure_notify"):
            E._try_row_sync(1)
        assert _sql_contains(conn, "next_attempt_at=now()+interval '10 min'"), "claim 应写认领锚"

    def test_finalize_guarded_by_sending(self):
        """终态回写带 AND status='sending'——回收-重领重叠时不双写。"""
        import src.email_service as E
        conn = _conn(scripted=[(1, "a@x", "s", "b", 0), None])   # claim 行 → 回写
        with patch("src.data_platform.db.get_conn", return_value=conn), \
             patch.object(E, "_send_email_sync", return_value=None), \
             patch("src.data_platform.log_sink.event"), \
             patch.object(E, "_final_failure_notify"):
            E._try_row_sync(1)
        assert _sql_contains(conn, "WHERE id=%s AND status='sending'"), "回写应带 sending 守卫"
