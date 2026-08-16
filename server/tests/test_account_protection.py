"""账户变更保护单测：guard_user_mutation（单一不变量：不能动自己）。

末位 admin 保护已移除（2026-08-15）：user_mgmt 仅 admin 持有 + 不能动自己
⇒ 最后一个 admin 不可能被他人变更（他人必是另一 admin ⇒ 目标非末位），原规则不可达。
"""
import pytest
from fastapi import HTTPException
from src.web_api.auth import guard_user_mutation


def test_cannot_mutate_self():
    """唯一不变量：不能动自己。"""
    with pytest.raises(HTTPException) as e:
        guard_user_mutation("alice", "alice")
    assert "当前登录" in e.value.detail


def test_can_mutate_other():
    """目标非自己 → 放行（不查 DB，末位 admin 由权限模型隐式保证）。"""
    guard_user_mutation("alice", "bob")  # 不抛


def test_guard_self_deactivate_last_admin():
    """自助注销路径：唯一启用 admin → 拒（管理页路径不可达，此路径真实可达）。"""
    import pytest
    from unittest.mock import patch, MagicMock
    from src.web_api.auth import guard_self_deactivate
    from src.web_api.errors import ApiError

    m = MagicMock()
    m.__enter__.return_value = m
    cur1 = MagicMock(); cur1.fetchone.return_value = ("admin",)   # 自己是启用 admin
    cur2 = MagicMock(); cur2.fetchone.return_value = (1,)          # 只剩 1 个
    m.execute.side_effect = [cur1, cur2]
    with patch("src.web_api.auth.get_conn", return_value=m):
        with pytest.raises(ApiError) as e:
            guard_self_deactivate(1)
    assert e.value.code == "LAST_ADMIN_PROTECTED"


def test_guard_self_deactivate_viewer_ok():
    """非 admin 自助注销 → 放行。"""
    from unittest.mock import patch, MagicMock
    from src.web_api.auth import guard_self_deactivate
    m = MagicMock()
    m.__enter__.return_value = m
    cur1 = MagicMock(); cur1.fetchone.return_value = ("viewer",)
    m.execute.side_effect = [cur1]
    with patch("src.web_api.auth.get_conn", return_value=m):
        guard_self_deactivate(9)  # 不抛
