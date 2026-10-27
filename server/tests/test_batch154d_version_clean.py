# -*- coding: utf-8 -*-
"""批 154 补④ · 系统组件版本串清洗（命令原文→纯版本）。

页面实证：nginx 行显示 "nginx version: nginx/1.24.0"、postgres 行 "psql (PostgreSQL) 18.4"
——脏串进台账后当前版本列不可读、与 repoquery 版本比较永不等。
"""
import pytest

from src.health_monitor.security_collector import _clean_component_version as f


@pytest.mark.parametrize("key,raw,want", [
    ("nginx", "nginx version: nginx/1.24.0", "1.24.0"),
    ("redis", "Redis server v=6.2.24 sha=00000000:0 malloc=jemalloc-5.1.0 bits=64", "6.2.24"),
    ("postgres", "psql (PostgreSQL) 18.4", "18.4"),
    ("pango", "pango-1.48.7", "1.48.7"),
    ("cairo", "cairo-1.17.4", "1.17.4"),
    ("wqy-microhei", "wqy-microhei-fonts-0.2.0", "0.2.0"),
    ("openssl", "1.1.1k", "1.1.1k"),
])
def test_clean(key, raw, want):
    assert f(key, raw) == want


def test_empty_and_unknown_passthrough():
    assert f("nginx", "") == ""
    assert f("weird", "no-version-here") == "no-version-here"
