"""发件箱指数退避单测：_backoff_seconds 曲线（1→2→4→8→16→30 分钟封顶）。"""
from src.web_api.email_service import _backoff_seconds, MAX_ATTEMPTS


def test_backoff_curve():
    assert _backoff_seconds(1) == 60        # 1 分钟
    assert _backoff_seconds(2) == 120       # 2 分钟
    assert _backoff_seconds(3) == 240       # 4 分钟
    assert _backoff_seconds(4) == 480       # 8 分钟
    assert _backoff_seconds(5) == 960       # 16 分钟


def test_backoff_cap_30min():
    assert _backoff_seconds(6) == 1800      # 30 分钟封顶
    assert _backoff_seconds(100) == 1800


def test_max_attempts():
    assert MAX_ATTEMPTS == 6
