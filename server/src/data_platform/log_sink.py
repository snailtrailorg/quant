"""批25：SystemLogHandler——system_log 统一落库（方案 v3 §2.2）。

设计要点（双盲审 A-P0/B-P0-2 吸收）：
- 内存 deque(2000) 防涨死 + 后台线程 5s/100 条批量 executemany；
- flush 幂等且线程安全（threading.Lock + drain-while）；
- 装配按进程分型（本模块提供 entry 函数，各入口自调）：
  * web-api：install() + FastAPI shutdown/atexit（不裸抢 SIGTERM——uvicorn 自装优雅退出）
  * celery worker/beat/risk：celery 信号 worker_process_init 内 install_worker()
    （线程不跨 fork；billiard os._exit 绕过 atexit → worker_shutdown 自冲刷）
  * live-task/md-hub/im-pool：install() + 自持 SIGTERM（链式调原 handler 后 sys.exit 让 atexit 跑）
- source：env QUANT_LOG_SOURCE（systemd 单元 Environment= 双写）；缺省按进程名兜底
- DB 故障降级：批量写失败丢批次+stderr 告警（日志系统不拖死主服务）
- level ≥INFO 落库（DEBUG 只控制台）；uvicorn.access propagate=false 天然不进（A-P2-8）
"""
from __future__ import annotations

import atexit
import logging
import os
import signal
import sys
import threading
import time
from collections import deque

_BATCH = 100
_INTERVAL = 5.0
_MAX_QUEUE = 2000

_sink: "_Sink | None" = None


class _Sink(logging.Handler):
    def __init__(self, source: str):
        super().__init__(level=logging.INFO)
        self.source = source
        self._q: deque[tuple[str, str, str]] = deque(maxlen=_MAX_QUEUE)
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._loop, name="log-sink", daemon=True)
        self._closed = False

    # level 归一（盲审 A-P2-7）：logger 面 WARNING/CRITICAL → WARN/ERROR——与 event() 及前端筛选枚举（ERROR/WARN/INFO）同值域
    @staticmethod
    def _norm_level(name: str) -> str:
        return {"WARNING": "WARN", "CRITICAL": "ERROR"}.get(name, name)

    def emit(self, record: logging.LogRecord) -> None:
        try:
            msg = self.format(record)
        except Exception:
            msg = record.getMessage()
        self._q.append((self._norm_level(record.levelname), record.name[:60], msg[:8000]))

    def _loop(self) -> None:
        while not self._stop.wait(_INTERVAL):
            self.flush()

    def flush(self) -> None:
        """批量落库；幂等+线程安全（关机钩子与后台线程并发时 Lock 串行，二次调用空转）。"""
        while True:
            with self._lock:
                if not self._q:
                    return
                batch = []
                while self._q and len(batch) < _BATCH:
                    batch.append(self._q.popleft())
            if not batch:
                return
            self._write(batch)

    def _write(self, batch: list[tuple[str, str, str]]) -> None:
        rows = [(level, self.source, module, msg) for level, module, msg in batch]
        try:
            from src.data_platform.db import get_conn
            with get_conn() as conn:
                # get_conn 返回 SQLAlchemy _ConnectionFairy——execute 有代理，executemany 走真 psycopg cursor
                with conn.cursor() as cur:
                    cur.executemany(
                        "INSERT INTO system_log (level, source, module, message) VALUES (%s, %s, %s, %s)",
                        rows)
                conn.commit()
        except Exception as e:   # 降级：丢批次不拖死主服务
            print(f"[log_sink] batch dropped ({len(rows)} rows): {e}", file=sys.stderr)

    def close(self) -> None:   # noqa: D102 —— 关机冲刷（幂等）
        if self._closed:
            return
        self._closed = True
        self._stop.set()
        try:
            self.flush()
        finally:
            if self._thread.is_alive():
                self._thread.join(timeout=3)


def _default_source() -> str:
    env = os.environ.get("QUANT_LOG_SOURCE")
    if env:
        return env
    return f"pid:{os.getpid()}"


def install(format_src: str = "") -> _Sink:
    """装配到 root logger（web-api/live-task/md-hub/im-pool 入口调用）。

    format_src 显式 source（如 live:{task_id}）；缺省 env QUANT_LOG_SOURCE→pid 兜底。
    atexit 兜底注册（SIGTERM 链由各入口自持或 uvicorn 优雅退出触发 sys.exit→atexit）。
    """
    global _sink
    if _sink is not None:
        return _sink
    _sink = _Sink(format_src or _default_source())
    root = logging.getLogger()
    root.setLevel(logging.INFO)   # 盲审 A-P0-1/B-P0：root 默认 WARNING 且后续 basicConfig 因已有 handler 短路——不设则 INFO 面全进程空转
    _sink.setFormatter(logging.Formatter("%(message)s"))
    root.addHandler(_sink)
    # 盲审 A-P0-1 后半：basicConfig 短路后 console StreamHandler 也没了（live-task/md-hub journalctl 全暗）——补装
    if not any(isinstance(h, logging.StreamHandler) and not isinstance(h, _Sink) for h in root.handlers):
        _sh = logging.StreamHandler()
        _sh.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s"))
        root.addHandler(_sh)
    _sink._thread.start()
    atexit.register(_sink.close)
    return _sink


def install_worker(format_src: str = "") -> _Sink:
    """celery 子进程装配（worker_process_init 信号内调用——fork 后线程不跨，每子进程自起）。

    billiard os._exit 绕过 atexit → worker_shutdown 信号处显式调 close()。
    """
    global _sink
    if _sink is not None:
        logging.getLogger().removeHandler(_sink)   # 盲审 A-P2-5：摘死 handler——不摘则子进程每条记录双写进无线程永不满发的父 deque
        _sink = None
    return install(format_src)


def chain_sigterm(previous=None) -> None:
    """自持 SIGTERM：冲刷后链式调原 handler 再退出（live-task/md-hub 等自管进程入口用）。
    盲审 B-P1：SIG_DFL/SIG_IGN 是整数枚举非 callable——排除后走 sys.exit（atexit 冲刷链保通）。"""
    prev = previous if callable(previous) else None
    def _handler(signum, frame):
        if _sink is not None:
            _sink.close()
        if prev is not None:
            prev(signum, frame)
        else:
            sys.exit(0)
    signal.signal(signal.SIGTERM, _handler)


def sink() -> _Sink | None:
    return _sink


def event(level: str, module: str, message: str) -> None:
    """结构化事件直写（三通道发送事件等非 logger 面调用——走同一批量管线）。"""
    if _sink is not None:
        _sink._q.append((level, module, message[:8000]))
    else:   # 未装配进程（如测试）：直插
        try:
            from src.data_platform.db import get_conn
            with get_conn() as conn:
                conn.execute(
                    "INSERT INTO system_log (level, source, module, message) VALUES (%s, %s, %s, %s)",
                    (level, _default_source(), module, message))
                conn.commit()
        except Exception as e:
            print(f"[log_sink] event dropped: {e}", file=sys.stderr)
