"""密码复杂度策略单测：validate_password（注册/重置/改密统一规则）。"""
import pytest
from src.web_api.auth import validate_password


def test_password_too_short():
    with pytest.raises(ValueError):
        validate_password("ab1")  # < 8


def test_password_no_letters():
    with pytest.raises(ValueError):
        validate_password("12345678")  # 仅数字


def test_password_no_digits():
    with pytest.raises(ValueError):
        validate_password("abcdefgh")  # 仅字母


def test_password_empty():
    with pytest.raises(ValueError):
        validate_password("")


def test_password_valid_letter_digit():
    validate_password("abcd1234")  # 字母+数字，≥8 → 通过
    validate_password("Aa1b2c3d")


def test_password_valid_with_special():
    validate_password("Abcd1234!")  # 含特殊字符也通过
