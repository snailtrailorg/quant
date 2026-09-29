"""调度层 —— Celery + beat。"""

from . import tasks as _tasks  # 注册任务
from .app import app

__all__ = ["app"]
