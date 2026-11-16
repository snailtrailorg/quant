"""批183d 守门：同名跨源包（pip/system）不互相覆盖判定列。"""


def test_judged_map_key_includes_source():
    import inspect
    from src.health_monitor import security_collector as sc
    s = inspect.getsource(sc.run_security_scan)
    assert 'r.get("group") == "os" else "pip"' in s
    assert "_upsert_components" in s


def test_upsert_lookup_includes_source():
    import inspect
    from src.health_monitor import security_collector as sc
    s = inspect.getsource(sc._upsert_components)
    assert "(source, _norm_key(name))" in s
