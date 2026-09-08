"""批10：pool_data._upsert_rows 直测——iterrows→to_dict("records") 改造中唯一
非 .get/[] 访问形态（row.items()+pd.notna 的 raw_json 过滤）的等价性锁。

既有引用全是 mock（test_pool_data_incremental.py patch 掉 _upsert_rows 本体），
该函数长期无直测裸奔。本文件锁三件事：
1. raw_json 内容：NaN 与 None 被过滤、键序=df 列序（dict.items() 保持插入序）
2. executemany 收到的 batch 行数与列数
3. 数值列 float 转换与 None 语义
"""
import json
from unittest.mock import MagicMock, patch

import pandas as pd

from src.data_sync import pool_data


def test_upsert_rows_raw_json_filter_and_order():
    df = pd.DataFrame({
        "ts_code": ["600000.SH", "600000.SH"],
        "end_date": ["20251231", "20241231"],
        "revenue": [100.5, float("nan")],   # 行 2 NaN：raw_json 过滤 + core 路径 float(nan)
        "note": ["abc", None],               # 非 core 非 pk 列：仅进 raw_json
    })
    with patch.object(pool_data._pdb, "get_conn") as g:
        cur = MagicMock()
        cur.fetchall.return_value = []
        conn = MagicMock()
        conn.cursor.return_value.__enter__.return_value = cur
        g.return_value.__enter__.return_value = conn
        saved = pool_data._upsert_rows(
            "income", ["ts_code", "end_date"], {"revenue": "revenue"}, df, "600000.SH")

    assert saved == 2
    sql, batch = cur.executemany.call_args[0]
    assert "raw_json" in sql                    # income ∈ raw_tables
    assert all(len(r) == 4 for r in batch)      # ts_code, end_date, revenue, raw_json

    # raw_json 等价性：键序=df 列序；NaN/None 行被过滤
    rj0 = json.loads(batch[0][3])
    assert list(rj0) == ["ts_code", "end_date", "revenue", "note"]
    assert rj0 == {"ts_code": "600000.SH", "end_date": "20251231",
                   "revenue": "100.5", "note": "abc"}
    rj1 = json.loads(batch[1][3])
    assert rj1 == {"ts_code": "600000.SH", "end_date": "20241231"}   # revenue NaN 与 note None 均滤除

    # core 列：行 1 float 化；行 2 NaN→float(nan)（沿用原语义，不改行为）
    assert batch[0][2] == 100.5
    import math
    assert math.isnan(batch[1][2])


def test_upsert_rows_empty_df():
    assert pool_data._upsert_rows("income", ["ts_code"], {}, pd.DataFrame(), "X") == 0
