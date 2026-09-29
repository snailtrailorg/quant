"""批 62c：SM 对账（security_master vs static_symbols 差集）测试钉。

批 79（2026-09-29）删除 shadow 行情对账，本文件由 shadow diff/预算/白名单测试改写为仅 SM 对账测试。
"""
from unittest.mock import MagicMock, patch


class TestSmReconcile:
    def test_diff_sets(self):
        """SM 差集：sm_only=库有清单无；static_only=清单有库无；ts_code→vt_symbol 归一；退市计数。"""
        from src.data_platform import quality as Q
        conn = MagicMock()
        conn.__enter__.return_value = conn
        # execute 三次：security_master(vt_symbol) → static_symbols 在市(ts_code) → static_symbols 退市
        conn.execute.side_effect = [
            MagicMock(fetchall=MagicMock(return_value=[("600000.SHSE",), ("000001.SZSE",)])),
            MagicMock(fetchall=MagicMock(return_value=[("600000.SH",), ("300750.SZ",)])),
            MagicMock(fetchall=MagicMock(return_value=[("000002.SZ",)])),
        ]
        with patch("src.data_platform.db.get_conn", return_value=conn):
            r = Q.sm_reconcile()
        assert r["sm_only"] == ["000001.SZSE"]       # 库有 000001、上市清单无
        assert r["static_only"] == ["300750.SZSE"]   # 上市清单有 300750、库无（ts_code 归一 .SZ→.SZSE）
        assert r["delist_count"] == 1

    def test_db_error_soft(self):
        """DB 异常 fail-soft：返回空差集不抛异常（delist_count 只在成功路径设置）。"""
        from src.data_platform import quality as Q
        with patch("src.data_platform.db.get_conn", side_effect=Exception("db down")):
            r = Q.sm_reconcile()
        assert r["sm_only"] == [] and r["static_only"] == []
