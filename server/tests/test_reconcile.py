"""三账对账 API 单测（#7）：返回 status + issues 结构。"""
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


def test_reconcile_api():
    from src.web_api.routes.risk import reconcile_api
    result = reconcile_api(payload={"username": "test", "role": "admin"})
    assert "status" in result
    assert "issues" in result
    assert isinstance(result["issues"], list)
