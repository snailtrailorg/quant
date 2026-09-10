"""批11E IM 绑定验证码测试（方案 v2 双盲审 A/B 修订后）。

覆盖：①归一化（全角/@前缀/空格）②match_consume（命中 GETDEL 单次/码错不绑+错 5 DEL/fid=None
不触发/群聊不触发/Redis 异常 fail-closed）③码生成条件（仅 ins+owner 非 NULL——task 侧钉子）
④拒答分支自动绑（条件插入防 TOCTOU——SQL 钉子+owner DB 现读）⑤admin status 过滤码。
"""
from unittest.mock import patch, MagicMock

import contextlib


def _conn(scripted=None):
    conn = MagicMock(); conn.__enter__.return_value = conn
    calls = {"n": 0}
    def execute(sql, *a):
        i = calls["n"]; calls["n"] += 1
        cur = MagicMock()
        if scripted and i < len(scripted):
            cur.fetchone.return_value, cur.fetchall.return_value, cur.rowcount = scripted[i]
        else:
            cur.fetchone.return_value = None
            cur.fetchall.return_value = []
            cur.rowcount = 0
        return cur
    conn.execute.side_effect = execute
    return conn


class TestNormalize:
    def test_full_width_and_mention_and_space(self):
        from src.im_bot.bindcode import _normalize
        assert _normalize("１２３４５６") == "123456"                    # 全角（B-P1-2 中文 IME）
        assert _normalize("@_user_1 123456") == "123456"                # 群聊 mention 前缀
        assert _normalize(" 1 2 3 4 5 6 \n") == "123456"                # 空白/换行
        assert _normalize("123456") == "123456"


class TestMatchConsume:
    def _rd(self, code="123456", getdel="123456"):
        rd = MagicMock()
        rd.get.return_value = code
        rd.getdel.return_value = getdel
        rd.incr.return_value = 1
        return rd

    def test_hit_consumes_once(self):
        from src.im_bot import bindcode as BC
        rd = self._rd()
        with patch.object(BC, "_client", return_value=rd):
            assert BC.match_consume(7, "123456", True) is True
        rd.getdel.assert_called_once()          # GETDEL 单次消费（A-P0-1）

    def test_wrong_code_not_bind_and_5_fails_del(self):
        from src.im_bot import bindcode as BC
        rd = self._rd()
        rd.incr.return_value = 1
        with patch.object(BC, "_client", return_value=rd):
            assert BC.match_consume(7, "999999", True) is False
        rd.getdel.assert_not_called()
        rd.incr.assert_called_once()            # 错次计数（A-P1-3）
        rd.delete.assert_not_called()           # 1 次<5 不作废
        rd.incr.return_value = 5
        with patch.object(BC, "_client", return_value=rd):
            assert BC.match_consume(7, "999999", True) is False
        rd.delete.assert_called()               # 第 5 次作废码

    def test_fid_none_and_group_not_trigger(self):
        from src.im_bot import bindcode as BC
        rd = self._rd()
        with patch.object(BC, "_client", return_value=rd):
            assert BC.match_consume(None, "123456", True) is False    # webhook fid=None（B-P2）
            assert BC.match_consume(7, "123456", False) is False      # 群聊（A-P1-4）
        rd.get.assert_not_called()

    def test_redis_error_fail_closed(self):
        from src.im_bot import bindcode as BC
        rd = MagicMock()
        rd.get.side_effect = RuntimeError("valkey down")
        with patch.object(BC, "_client", return_value=rd):
            assert BC.match_consume(7, "123456", True) is False       # 异常不阻断原拒答流


class TestTaskIssue:
    """码生成条件钉子：仅 ins 真新建+owner 非 NULL（重扫/平台级/admin 不发——B-P1-1/A-P2）。"""

    def _run(self, row_owner, task_owner):
        import src.feishu_bot.tasks as T
        conn = MagicMock(); conn.__enter__.return_value = conn
        calls = {"n": 0}
        def exe(sql, *a):
            calls["n"] += 1
            cur = MagicMock()
            # 首查=SELECT 既有行（row_owner None=无既有行→首查 None 走 INSERT）
            if calls["n"] == 1 and "RETURNING" not in sql:
                cur.fetchone.return_value = (10, "enc", row_owner) if row_owner is not None else None
            elif "RETURNING" in sql:
                cur.fetchone.return_value = (99,) if row_owner is None else None   # 无既有行→INSERT 真插入
            else:
                cur.fetchone.return_value = None
            return cur
        conn.execute.side_effect = exe
        states = []
        fake = MagicMock(); fake.register_app.return_value = {"client_id": "cli_z", "client_secret": "s"}
        with patch("src.feishu_bot.tasks.get_conn", return_value=conn), \
             patch.object(T.lark, "register_app", fake.register_app), \
             patch.object(T, "_set_session", lambda sid, d, expire=600, owner_user_id=None: states.append(d)), \
             patch("src.im_bot.credentials.save_bot_credentials", MagicMock()), \
             patch("src.im_bot.credentials.get_bot_credentials", return_value={}), \
             patch.object(T, "_audit"), \
             patch.object(T, "_current_username", return_value="u9"), \
             patch("src.im_bot.bindcode.issue") as p_issue, \
             patch("httpx.post", return_value=MagicMock(json=lambda: {})), \
             patch("httpx.get", return_value=MagicMock(json=lambda: {})):
            T.run_onboarding("s", owner_user_id=task_owner)
        return states, p_issue

    def test_new_bot_self_service_issues_code(self):
        states, issue = self._run(row_owner=None, task_owner=9)   # 无既有行→INSERT 新建
        assert issue.called                                          # 自助新建发码
        assert states[-1].get("bind_code")                           # done 载荷带码

    def test_rescan_own_no_code(self):
        states, issue = self._run(row_owner=9, task_owner=9)      # 既有自己行→重扫分支
        assert not issue.called
        assert "bind_code" not in (states[-1] or {})

    def test_admin_no_code(self):
        states, issue = self._run(row_owner=None, task_owner=None)
        assert not issue.called                                      # admin 面（owner=None）不发


class TestAutoBindSQL:
    """拒答分支条件插入钉子（A-P0-1：防 TOCTOU 双绑）+owner DB 现读（A-P1-1）。"""

    def test_conditional_insert_sql_pinned(self):
        import pathlib
        src = pathlib.Path("src/im_bot/feishu_client.py").read_text()
        assert "bind_owner(fid, open_id, _owner)" in src and "user_id IS NOT NULL" in src   # P0-2 修：原语+已绑拦截
        assert "SELECT owner_user_id FROM im_bot_config WHERE id=%s" in src   # DB 现读非缓存

    def test_admin_status_filters_code(self):
        import pathlib
        src = pathlib.Path("src/web_api/routes/im_bots.py").read_text()
        assert 'd.pop("bind_code", None)' in src


class TestCodeBranchBehavior:
    """代码双盲审 P0 修复的行为级钉子（原字符串 grep 对 P0 全失明——B-P1-1）。"""

    def _run_process(self, chat_type, code_hit, owner_row, bound_row, code="123456", text="123456",
                     ident_seq=None):
        """mock 驱动 process_message_async 到码分支。"""
        import src.im_bot.feishu_client as FC
        conn = MagicMock(); conn.__enter__.return_value = conn
        # 调用序：码分支 owner SELECT → 已绑 SELECT →（bind_owner 不走 conn）→ resolve 复验
        states = {"n": 0}
        def exe(sql, *a):
            cur = MagicMock()
            if "owner_user_id FROM im_bot_config" in sql:
                cur.fetchone.return_value = owner_row
            elif "user_id IS NOT NULL" in sql:
                cur.fetchone.return_value = bound_row
            else:
                cur.fetchone.return_value = None
            return cur
        conn.execute.side_effect = exe
        sent = []
        client = MagicMock()
        client.send_text.side_effect = lambda rid, txt, rtype=None: sent.append(txt)
        import src.im_bot.bindcode as BC
        rd = MagicMock()
        rd.get.return_value = code if code_hit else None
        rd.getdel.return_value = code
        ident = {"user_id": 9, "username": "u9", "role": "viewer", "perms": {"read"}}
        with patch("src.data_platform.db.get_conn", return_value=conn), \
             patch("src.im_bot.users.resolve_im_identity", side_effect=ident_seq or [None, ident, None]), \
             patch.object(FC, "get_feishu_client", return_value=client), \
             patch.object(BC, "_client", return_value=rd), \
             patch("src.im_bot.users.bind_owner") as p_bind:
            FC.process_message_async("ou_x", text, "open_id", "ou_x", fid=7, chat_type=chat_type)
        return p_bind, sent

    def test_hit_p2p_binds_and_replies(self):
        p_bind, sent = self._run_process("p2p", code_hit=True, owner_row=(9,), bound_row=None)
        assert p_bind.called                                  # bind_owner 原语（P0-2：非裸 INSERT）
        assert sent and "绑定成功" in sent[0]

    def test_group_chat_not_trigger(self):
        p_bind, sent = self._run_process("group", code_hit=True, owner_row=(9,), bound_row=None)
        assert not p_bind.called and "绑定成功" not in "".join(sent)   # P0-3：判据=chat_type（拒答照发=预期）

    def test_already_bound_not_rebind(self):
        p_bind, sent = self._run_process("p2p", code_hit=True, owner_row=(9,), bound_row=(1,))
        assert not p_bind.called and "绑定成功" not in "".join(sent)   # 已绑拦截（防御纵深）

    def test_no_owner_code_consumed_but_no_bind(self):
        # 文案师版 except 分支会重查身份——owner 空场景全程未绑定（含重查），落拒答不发"绑定成功"
        p_bind, sent = self._run_process("p2p", code_hit=True, owner_row=None, bound_row=None,
                                         ident_seq=[None, None, None])
        assert not p_bind.called and "绑定成功" not in "".join(sent)   # owner 空码白吃——落拒答（兜底 try 不崩）


class TestEndpoints:
    """端点级断言（P0-1：pop 加错端点曾致功能 DOA）。"""

    def test_self_status_returns_code_admin_filtered(self):
        """自助面返回码（前端显示源）；admin 面 pop 码。"""
        import pathlib
        src = pathlib.Path("src/web_api/routes/im_bots.py").read_text()
        # 自助端点（my_im_onboarding_status）不得 pop；admin 端点（im_bots_onboarding_status）必 pop
        admin_seg = src.split("def my_im_onboarding_status")[0]
        self_seg = src.split("def my_im_onboarding_status")[1].split("@router")[0]
        assert 'd.pop("bind_code", None)' in admin_seg
        assert 'd.pop' not in self_seg
