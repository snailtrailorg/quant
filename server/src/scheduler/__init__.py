"""调度层 —— Celery + beat。"""

from .app import app
from . import tasks as _tasks  # 注册任务

__all__ = ["app"]
