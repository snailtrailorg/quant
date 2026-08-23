"""调度层 —— Celery + beat 定时任务。

时区 Asia/Shanghai；A 股任务带 is_trading_day 跳过非交易日。
启动: celery -A src.scheduler.app worker -B --loglevel=info
  （并发度从 system_config.celery_concurrency 读，不再用 -c 硬编码；运行时 Web 可动态调）
"""

from __future__ import annotations
import os
from datetime import date, datetime
from celery import Celery
from celery.schedules import crontab
from dotenv import load_dotenv

load_dotenv()

VALKEY_URL = os.environ.get("VALKEY_URL", "redis://127.0.0.1:6379/0")
# SE1（F-35）：celery 独立 db（.env 的 CELERY_BROKER_URL/CELERY_RESULT_BACKEND 此前从未被读，
# broker/backend 直连 VALKEY_URL=db0 与业务键（熔断/JWT 黑名单/锁/去重）混装——一次故障全带走
CELERY_BROKER_URL = os.environ.get("CELERY_BROKER_URL") or VALKEY_URL
CELERY_RESULT_BACKEND = os.environ.get("CELERY_RESULT_BACKEND") or CELERY_BROKER_URL


def _load_celery_concurrency() -> int:
    """从 system_config 表读 celery_concurrency（DB 优先，fallback 环境变量/默认 2）。

    worker 启动时调用一次；运行时由 Web API 动态 pool_grow/shrink 调整。
    """
    try:
        from src.data_platform.db import get_conn
        with get_conn() as conn:
            cur = conn.execute("SELECT value FROM system_config WHERE key='celery_concurrency'")
            r = cur.fetchone()
            if r:
                return int(r[0])
    except Exception:
        pass  # 表未建或 DB 不可达，用 fallback
    return int(os.environ.get("CELERY_CONCURRENCY", "2"))


app = Celery(
    "quant",
    broker=CELERY_BROKER_URL,
    backend=CELERY_RESULT_BACKEND,
    include=["src.scheduler.tasks", "src.feishu_bot.tasks"],
)

# 自定义因子加载（链条打磨#1：worker 进程此前永不加载——回测/实盘"未知因子"直接失败）
try:
    from src.strategy_framework.factor import load_factors_from_db
    _loaded_f = load_factors_from_db()
    if _loaded_f:
        print(f"✓ 加载自定义因子: {', '.join(_loaded_f)}")
except Exception:
    pass   # 表未建/DB 未就绪的早期导入窗口静默

# #48：列级校验挂 celery 父进程（import 期一次；prefork 子进程 fork 不重复执行）
try:
    from src.data_platform.db import verify_schema
    from src.health_monitor.monitor import report_schema_findings
    report_schema_findings(verify_schema())
except Exception:
    pass   # broker/db 未就绪的极早期导入窗口静默（web/runner 入口会再报）

app.conf.update(
    timezone="Asia/Shanghai",
    enable_utc=True,
    task_serializer="json",
    accept_content=["json"],
    worker_concurrency=_load_celery_concurrency(),  # 从 system_config 读（运行时可动态调）
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
            "schedule": 300.0,   # U-4: 300s 才对得上 cron 窗口（08:45 等分钟级 schedule）
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
        "email-outbox-sweep": {
            "task": "src.scheduler.tasks.email_outbox_sweep",
            "schedule": 60.0,
            "options": {"queue": "risk"},
        },
        "notifications-cleanup": {
            "task": "src.scheduler.tasks.notifications_cleanup",
            "schedule": 86400.0,
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
        "daily-report": {
            "task": "src.scheduler.tasks.daily_report",
            "schedule": crontab(hour=16, minute=30),
            "options": {"queue": "risk"},
        },
        "broker-health-check": {
            "task": "src.scheduler.tasks.broker_health_check",
            "schedule": 21600.0,
            "options": {"queue": "risk"},
        },
        # 15-服务监控：30s 症状型判定（unit/依赖/心跳，沿检测去重），S6 修订配套
        # 池内深度数据同步（三档第二档：财务/筹码/股东 per-symbol，Tushare 5000 积分内免费）
        "pool-data-sync": {
            "task": "src.scheduler.tasks.pool_data_sync_task",
            "schedule": 300.0,
            "options": {"queue": "data", "expires": 290},
        },
        # 二档周日全量校准（O-F1/6）：增量窗口兜底——迟到公告/上游改历史/长期失败冻结的
        # 游标（full 推进游标=窗口解冻）。04:07 错峰（避开 04:00 备份/03:xx 其他任务）
        "pool-data-full-calibrate": {
            "task": "src.scheduler.tasks.pool_data_sync_task",
            "schedule": crontab(day_of_week=0, hour=4, minute=7),
            "options": {"queue": "data", "expires": 3600},
            "kwargs": {"full": True},
        },
        # 池分钟同步（已建未启用——Tushare stk_mins 是独立产品包 2000 元/年，
        # 全局 1 次/小时不够用；先靠 XTP hub 自攒，买包后启用 Tushare 为主源+XTP 校验。
        # 启用方法：取消注释此 beat + data_source_config.params 配 rate_limits）
        # "pool-minute-sync": {
        #     "task": "src.scheduler.tasks.pool_minute_sync_task",
        #     "schedule": 300.0,
        #     "options": {"queue": "data", "expires": 290},
        # },
        "health-monitor": {
            "task": "src.scheduler.tasks.health_monitor_check",
            "schedule": 30.0,
            # expires：worker 停机期间过期消息丢弃，防恢复后连环补跑（盲审 D 陷阱 7）
            "options": {"queue": "risk", "expires": 25},
        },
        # SA4：Failed 实盘单元 reconciler（CrashLoopBackOff 退避自动 reset-failed + start）
        "sa4-reconciler": {
            "task": "src.scheduler.tasks.sa4_reconciler",
            "schedule": 300.0,
            "options": {"queue": "risk", "expires": 290},
        },
    },
)