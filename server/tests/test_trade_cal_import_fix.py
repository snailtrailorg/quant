"""批 94 回归钉：pull_trade_cal 的坏相对导入。

历史缺陷：`tushare_adapter.pull_trade_cal` 内 `from .db import get_conn`——adapters 包内
**没有 db.py**（正确层级 `..db`，同文件 :350/:439 均为 `..db`）⇒ 该行 ModuleNotFoundError，
`trade_cal` 同步自引入起一直失败（staging 游标停在 20260725 即铁证），且**全量测试绿**——
没有任何用例覆盖此函数。本钉：真调用 pull_trade_cal（DB/get_pro 全 mock），回退修复即红。
"""
from unittest.mock import MagicMock, patch

import pandas as pd

from src.data_platform.adapters import tushare_adapter


def test_pull_trade_cal_imports_and_writes_rows():
    """坏导入若被回退（..db → .db），此用例在函数内 import 时即 ModuleNotFoundError。"""
    df = pd.DataFrame([
        {"exchange": "SSE", "cal_date": "2026-01-01", "is_open": 0, "pretrade_date": None},
        {"exchange": "SSE", "cal_date": "2026-01-05", "is_open": 1, "pretrade_date": "2026-01-01"},
    ])
    fake_pro = MagicMock()
    fake_pro.trade_cal.return_value = df
    fake_conn = MagicMock()
    cm = MagicMock()
    cm.__enter__.return_value = fake_conn
    cm.__exit__.return_value = False
    with patch.object(tushare_adapter, "get_pro", return_value=fake_pro), \
         patch("src.data_platform.db.init_trade_calendar"), \
         patch("src.data_platform.db.get_conn", return_value=cm):
        rows = tushare_adapter.pull_trade_cal(2026)
    assert rows == [("SSE", "2026-01-01", 0, None), ("SSE", "2026-01-05", 1, "2026-01-01")]
    assert fake_conn.cursor.return_value.__enter__.return_value.executemany.called
    assert fake_conn.commit.called


def test_pull_trade_cal_empty_frame_no_db_touch():
    """空帧早退：不触 DB（init_trade_calendar 不被调）。"""
    fake_pro = MagicMock()
    fake_pro.trade_cal.return_value = pd.DataFrame()
    with patch.object(tushare_adapter, "get_pro", return_value=fake_pro), \
         patch("src.data_platform.db.init_trade_calendar") as fake_init, \
         patch("src.data_platform.db.get_conn") as fake_get:
        assert tushare_adapter.pull_trade_cal(2026) == []
    assert not fake_init.called and not fake_get.called
