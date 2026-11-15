"""批182c 守门：契约缺失不得清空判定列；续跑窗=单轮上限而非 26h。"""


def test_upsert_components_coalesce_preserves_judgement():
    """契约缺失时 judged 为空，SQL 必须 COALESCE 保留旧 latest/judge（否则全表清空）。"""
    import inspect
    from src.health_monitor import security_collector as sc
    s = inspect.getsource(sc._upsert_components)
    assert "latest_version=COALESCE(" in s
    assert "judge_class=COALESCE(" in s


def test_resume_window_is_two_hours_not_26h():
    """续跑窗=单轮上限 2h；26h 会让 24h 后的新轮被上一轮整轮罩住。"""
    import inspect
    from src.scheduler import tasks
    s = inspect.getsource(tasks.dep_audit)
    assert "timedelta(hours=2)" in s
    assert "timedelta(hours=26)" not in s
