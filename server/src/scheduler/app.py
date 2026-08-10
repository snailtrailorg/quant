"""调度层 —— Celery + beat 定时任务。

时区 Asia/Shanghai；A 股任务带 is_trading_day 跳过非交易日。
启动: celery -A src.scheduler.app worker -B -c 2 --loglevel=info
"""

from __future__ import annotations
import os
from datetime import date, datetime
from celery import Celery
from dotenv import load_dotenv

load_dotenv()

VALKEY_URL = os.environ.get("VALKEY_URL", "redis://127.0.0.1:6379/0")

app = Celery(
    "quant",
    broker=VALKEY_URL,
    backend=VALKEY_URL,
    include=["src.scheduler.tasks", "src.feishu_bot.tasks"],
)

app.conf.update(
    timezone="Asia/Shanghai",
    enable_utc=True,
    task_serializer="json",
    accept_content=["json"],
    worker_concurrency=2,  # 低配 ECS 限流
    task_track_started=True,
    task_soft_time_limit=300,  # 5 分钟超时
    beat_schedule={
        "data-increment-daily": {
            "task": "src.scheduler.tasks.data_increment_daily",
            "schedule": 86400.0,  # 每天一次（实盘用 crontab hour=16, minute=0）
            "options": {"queue": "data"},
        },
        "astock-select-daily": {
            "task": "src.scheduler.tasks.astock_select_daily",
            "schedule": 86400.0,
            "options": {"queue": "analysis"},
        },
        "data-increment-crypto": {
            "task": "src.scheduler.tasks.data_increment_crypto",
            "schedule": 900.0,  # 15min
            "options": {"queue": "data"},
        },
        "sync-scheduler": {
            "task": "src.scheduler.tasks.data_sync_scheduler",
            "schedule": 1800.0,
            "options": {"queue": "data"},
        },
        "disk-monitor": {
            "task": "src.scheduler.tasks.disk_monitor",
            "schedule": 21600.0,
            "options": {"queue": "risk"},
        },
        "data-continuity": {
            "task": "src.scheduler.tasks.data_continuity_check",
            "schedule": 3600.0,
            "options": {"queue": "data"},
        },
        "reconcile": {
            "task": "src.scheduler.tasks.reconcile_three_books",
            "schedule": 3600.0,
            "options": {"queue": "risk"},
        },
        "drift-check": {
            "task": "src.scheduler.tasks.drift_check",
            "schedule": 86400.0,
            "options": {"queue": "risk"},
        },
        "risk-sweep": {
            "task": "src.scheduler.tasks.risk_sweep",
            "schedule": 60.0,
            "options": {"queue": "risk"},
        },
        "convertible-terms-sync": {
            "task": "src.scheduler.tasks.convertible_terms_sync",
            "schedule": 86400.0,
            "options": {"queue": "data"},
        },
        "budget-alert-check": {
            "task": "src.scheduler.tasks.budget_alert_check",
            "schedule": 3600.0,
            "options": {"queue": "risk"},
        },
        "static-list-sync": {
            "task": "src.scheduler.tasks.static_list_sync",
            "schedule": 604800.0,
            "options": {"queue": "data"},
        },
        "broker-health-check": {
            "task": "src.scheduler.tasks.broker_health_check",
            "schedule": 21600.0,
            "options": {"queue": "risk"},
        },
    },
)