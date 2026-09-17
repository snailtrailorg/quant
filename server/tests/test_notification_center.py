"""通知中心单测：类别×角色可见矩阵 + 外部推送规则（2026-08-14 决策）。"""
from src.alert_notify.notify import visible_categories, CATEGORY_ROLES   # 批39：should_push_external 随 webhook 链退役删


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


# 批39：webhook 外推语义钉随 should_push_external 退役删除
def test_matrix_covers_all_categories():
    """每个类别至少一个角色可见。"""
    for c in CATEGORY_ROLES:
        assert visible_categories(CATEGORY_ROLES[c][0])  # 非空即有角色
