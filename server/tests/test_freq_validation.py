"""F2 回归（2026-08-18 盲审定案）：freq 校验大小写不敏感。

背景：a28a5fa 引入 `_VALID_FREQS` 小写集合，写路径历史全用 "1D" → save_bars assert 必炸，
异常被 data_sync 逐日吞掉 → 日线同步静默断 11 天（游标照推）。本测试锁死大写形态可用。
"""
from unittest.mock import patch, MagicMock


def _row(ts="2026-08-18"):
    return ("600000.SHSE", "1D", ts, 9.0, 9.1, 8.9, 9.05, 100000, 905000, None, "tushare")


def test_save_bars_accepts_uppercase_1D():
    """写路径历史统一用 "1D"（engine.py:448/tasks.py:354/platform.py）——必须过校验。"""
    from src.data_platform import db
    mock_conn = MagicMock()
    mock_conn.__enter__.return_value = mock_conn
    with patch.object(db, "get_conn", return_value=mock_conn), \
         patch.object(db, "validate_bars", side_effect=lambda rows: rows), \
         patch.object(db, "ensure_table") as mock_ensure:
        n = db.save_bars("1D", [_row()])
    assert n == 1
    mock_ensure.assert_called_once_with("1D")


def test_save_bars_rejects_garbage_freq():
    from src.data_platform import db
    try:
        db.save_bars("2min", [_row()])
        raised = False
    except AssertionError:
        raised = True
    assert raised, "非法 freq 仍应被拒"
