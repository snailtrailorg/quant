"""沙箱 web 桩：/healthz 与 /readyz 返回 200 + 本版 release_id（读 cwd/RELEASE——
run-current 已 cd 到 releases/<id>，healthz 回带的即进程实际运行版本）。

场景 3（crash-loop）：cwd 存在 crash.web 标记则启动即退——Restart=always 拉锯后
StartLimitBurst=2 判死，被 release.yml 波次 dwell 即刻捕获 → 验证层穿透 → 回滚。
"""
from __future__ import annotations

import json
import os
import sys
from http.server import BaseHTTPRequestHandler, HTTPServer

MARK = "src/crash.web"


def _release() -> str:
    try:
        with open("RELEASE", encoding="utf-8") as fh:
            return fh.read().strip()
    except OSError:
        return "unknown"


class Handler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:  # noqa: N802 —— http.server 约定
        if self.path in ("/healthz", "/readyz"):
            body = json.dumps({"release": _release(), "path": self.path}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        else:
            self.send_response(404)
            self.end_headers()

    def log_message(self, *_args) -> None:  # 静音访问日志
        pass


if os.path.exists(MARK):
    print(f"✗ crash 标记存在（{MARK}），启动即退", file=sys.stderr)
    sys.exit(1)

port = int(os.environ.get("SBX_WEB_PORT", "18923"))
HTTPServer(("127.0.0.1", port), Handler).serve_forever()
