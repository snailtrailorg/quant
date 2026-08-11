"""数据源单测：TushareDataSource（token 解密 + .env fallback + get_client）。"""
import os
from unittest.mock import patch


def test_tushare_env_fallback():
    """无 DB 配置，token 从 .env 读"""
    from src.data_platform.data_source import TushareDataSource
    ds = TushareDataSource(credentials_encrypted=None)
    with patch.dict(os.environ, {"TUSHARE_TOKEN": "env-token"}):
        assert ds._get_token() == "env-token"


def test_tushare_db_token_decrypt():
    """有 DB 配置，token 解密"""
    from src.data_platform.data_source import TushareDataSource
    with patch("src.web_api.crypto_utils.decrypt", return_value="db-token"):
        ds = TushareDataSource(credentials_encrypted="enc-blob")
        assert ds._get_token() == "db-token"


def test_tushare_get_client():
    """get_client 调 ts.pro_api(token)"""
    from src.data_platform.data_source import TushareDataSource
    ds = TushareDataSource(credentials_encrypted=None)
    with patch.dict(os.environ, {"TUSHARE_TOKEN": "t"}), patch("tushare.pro_api") as m:
        ds.get_client()
        m.assert_called_once_with("t")


def test_get_data_source_unknown_provider():
    """未注册 provider 返回 None（不查 DB）"""
    from src.data_platform.data_source import get_data_source
    assert get_data_source("unknown_provider_xyz") is None
