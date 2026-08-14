"""通知中心单测：类别×角色可见矩阵 + 外部推送规则（2026-08-14 决策）。"""
from src.alert_notify.notify import visible_categories, should_push_external, CATEGORY_ROLES


def test_admin_sees_all():
    cats = visible_categories("admin")
    assert set(cats) == {"email", "risk", "task", "data", "system"}


def test_email_only_admin():
    """邀请/邮件失败通知只有 admin 看得懂（用户决策）。"""
    assert "email" in visible_categories("admin")
    assert "email" not in visible_categories("trader")
    assert "email" not in visible_categories("analyst")
    assert "email" not in visible_categories("viewer")


def test_trader_sees_risk_task():
    cats = visible_categories("trader")
    assert "risk" in cats and "task" in cats
    assert "data" not in cats and "system" not in cats


def test_analyst_sees_data():
    cats = visible_categories("analyst")
    assert cats == ["data"]


def test_viewer_sees_nothing():
    assert visible_categories("viewer") == []


def test_external_push_only_risk_critical():
    """外部通道只推实盘紧急（risk+critical）；其余站内。"""
    assert should_push_external("risk", "critical") is True
    assert should_push_external("risk", "warn") is False
    assert should_push_external("email", "critical") is False   # 邮件失败不外推
    assert should_push_external("system", "critical") is False  # 磁盘/接口也不外推
    assert should_push_external("task", "warn") is False


def test_matrix_covers_all_categories():
    """每个类别至少一个角色可见。"""
    for c in CATEGORY_ROLES:
        assert visible_categories(CATEGORY_ROLES[c][0])  # 非空即有角色
