"""因子 API 单测（#2）：list_factors 端点返回注册表。"""
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


def test_list_factors_api():
    from src.web_api.main import list_factors_api
    result = list_factors_api(payload={"username": "test", "role": "admin"})
    assert "items" in result
    assert isinstance(result["items"], list)
    # 因子注册表有预置因子（ma_dev/rsi 等）
    if result["items"]:
        assert "name" in result["items"][0]
