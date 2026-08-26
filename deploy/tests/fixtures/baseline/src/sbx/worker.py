"""沙箱 celery 桩：常驻睡眠（进程 cwd=releases/<id>，供版本收敛断言采样）。"""
from __future__ import annotations

import os
import sys
import time

if os.path.exists("src/crash.celery"):
    print("✗ crash 标记存在（crash.celery），启动即退", file=sys.stderr)
    sys.exit(1)

while True:
    time.sleep(60)
