"""沙箱 hub 桩：每秒写心跳 JSON（gen+8 字段）到 SBX_HB_FILE，供 postverify 心跳复现断言。

字段集对齐生产 quant:hb:md-hub 语义（gen + 8 字段）；文件模式为沙箱道具
（生产走 redis 模式，见 group_vars deploy_hub_hb_mode）。
"""
from __future__ import annotations

import json
import os
import sys
import time
from datetime import datetime, timezone

if os.path.exists("src/crash.hub"):
    print("✗ crash 标记存在（crash.hub），启动即退", file=sys.stderr)
    sys.exit(1)

hb_file = os.environ.get("SBX_HB_FILE")
if not hb_file:
    print("✗ 缺 SBX_HB_FILE 环境变量", file=sys.stderr)
    sys.exit(2)

gen = int(time.time())  # 进程代次（沙箱以启动时刻近似）
while True:
    hb = {
        "gen": gen,
        "ts": datetime.now(timezone.utc).isoformat(),
        "unit": "quant-sbx-hub",
        "state": "running",
        "bar_count": 100,
        "stream_lag_ms": 3,
        "xtp_ok": True,
        "valkey_ok": True,
        "lease": "sbx",
    }
    tmp = hb_file + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(hb, fh)
    os.replace(tmp, hb_file)  # 原子写，读方不见半截 JSON
    time.sleep(1)
