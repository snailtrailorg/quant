"""批170：dep 台账读取的根因回归（2026-11-04 17:4x 连续降级事故）。

事故链：扫描收尾把未审计行 checked_at 改成扫描时间 → 读取侧 checked_at<=last_complete 切面
把整批行切空 → 整 components 分区降级 → 安全页只显示历史漏洞。
本测试钉死三条不变量：
  1) 切面过滤为空时必须回退全表（mixed=True），不得降级；
  2) 表真空才 fail-loud；
  3) 收尾回填 SQL 不得改写 checked_at（切面锚）。
"""
from __future__ import annotations

import re
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from src.health_monitor import security_collector as sc


def _patch_conn(filtered, all_rows):
    @contextmanager
    def _cm():
        conn = MagicMock()
        conn.execute.side_effect = lambda sql, args=(): MagicMock(
            fetchall=lambda: (filtered if "COALESCE(checked_at" in sql else all_rows))
        conn.__enter__ = lambda s: conn
        conn.__exit__ = lambda *a: False
        yield conn
    return patch("src.data_platform.db.get_conn", _cm)


ROWS = [("numpy", "1.0", "1.1", "actionable", "仓库可升 1.1", None)]


def test_empty_cutoff_falls_back_to_all_rows():
    """切面过滤为空 → 回退全表 + mixed=True（事故当天的形态）。"""
    with _patch_conn(filtered=[], all_rows=ROWS):
        rows, mixed = sc.read_dep_rows("2026-11-04T05:00:31+00:00")
    assert rows == ROWS and mixed is True


def test_cutoff_hit_is_not_mixed():
    with _patch_conn(filtered=ROWS, all_rows=ROWS):
        rows, mixed = sc.read_dep_rows("2026-11-04T05:00:31+00:00")
    assert rows == ROWS and mixed is False


def test_empty_table_fails_loud():
    """真无真源（表零行）必须降级，不得静默空表。"""
    with _patch_conn(filtered=[], all_rows=[]):
        with pytest.raises(RuntimeError, match="表为空"):
            sc.read_dep_rows("2026-11-04T05:00:31+00:00")


def test_backfill_does_not_touch_checked_at():
    """收尾回填只能改 judge 字段——写 checked_at 会再次把切面切空（根因①防回归）。"""
    src = Path(sc.__file__).read_text(encoding="utf-8")
    m = re.search(r"UPDATE security_component SET judge_class=%s.*?WHERE judge_class IS NULL", src, re.S)
    assert m, "未找到未审计行回填 SQL"
    assert "checked_at" not in m.group(0), "回填 SQL 不得改写 checked_at（切面锚）"
