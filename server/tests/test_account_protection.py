"""账户变更保护单测：guard_user_mutation（自我锁定 + 管理锁定两条不变量）。"""
import pytest
from unittest.mock import patch, MagicMock
from fastapi import HTTPException
from src.web_api.auth import guard_user_mutation


def test_cannot_mutate_self():
    """规则1：不能动自己（无论是否剥夺 admin）。"""
    with pytest.raises(HTTPException) as e:
        guard_user_mutation("alice", "alice", removes_admin=False)
    assert "当前登录" in e.value.detail


def test_can_mutate_other_when_not_removing_admin():
    """目标非自己、且不剥夺 admin → 放行（不查 DB）。"""
    guard_user_mutation("alice", "bob", removes_admin=False)  # 不抛


def _mock_conn_admin_count(count):
    mock = MagicMock()
    mock.__enter__.return_value = mock  # with get_conn() as conn → conn 即 mock
    cur = MagicMock()
    cur.fetchone.return_value = (count,)
    mock.execute.return_value = cur
    return mock


def test_cannot_remove_last_enabled_admin():
    """规则2：剥夺最后一个启用 admin（count=1）→ 拒。"""
    with patch("src.web_api.auth.get_conn", return_value=_mock_conn_admin_count(1)):
        with pytest.raises(HTTPException) as e:
            guard_user_mutation("alice", "bob", removes_admin=True)
    assert "最后一个启用" in e.value.detail


def test_can_remove_admin_when_others_exist():
    """规则2：还有其他启用 admin（count=2）→ 放行。"""
    with patch("src.web_api.auth.get_conn", return_value=_mock_conn_admin_count(2)):
        guard_user_mutation("alice", "bob", removes_admin=True)  # 不抛
