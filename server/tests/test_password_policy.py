"""密码复杂度策略单测：validate_password（细分 ApiError 错误码，前端 err.<CODE> 本地化）。"""
import pytest
from fastapi import HTTPException
from src.web_api.auth import validate_password
from src.web_api.errors import ApiError


def _code(pw):
    with pytest.raises(ApiError) as e:
        validate_password(pw)
    return e.value.code


def test_password_too_short():
    assert _code("ab1") == "PASSWORD_TOO_SHORT"


def test_password_no_letters():
    assert _code("12345678") == "PASSWORD_NO_LETTER"


def test_password_no_digits():
    assert _code("abcdefgh") == "PASSWORD_NO_DIGIT"


def test_password_empty():
    assert _code("") == "PASSWORD_TOO_SHORT"


def test_password_valid():
    validate_password("abcd1234")   # 不抛
    validate_password("Aa1b2c3d")


def test_api_error_shape():
    """ApiError 兼容 HTTPException（detail 字符串）+ 携带 code。"""
    e = ApiError(400, "X_CODE", "中文兜底")
    assert isinstance(e, HTTPException)
    assert e.status_code == 400 and e.detail == "中文兜底" and e.code == "X_CODE"
