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
    include=["src.scheduler.tasks", "src.scheduler.alert_tasks"],   # 批12A：feishu onboarding 去 celery 化摘除
)

# 批25：system_log 落库——celery 侧装配（盲审 A-P0 三连击根治：hijack_root_logger 摘 root → setup_logging 挂回；
# prefork 线程不跨 fork → worker_process_init 每子进程自起；billiard os._exit 绕 atexit → worker_shutdown 自冲刷）
from celery.signals import (setup_logging as _cel_setup_logging, worker_process_init,
                             worker_shutdown, worker_process_shutdown)

@_cel_setup_logging.connect
def _on_celery_setup_logging(**_kw):
    import logging
    from src.data_platform.log_sink import install_worker
    install_worker(os.environ.get("QUANT_LOG_SOURCE") or "celery")
    return logging.getLogger()   # 返回 root：celery 不再自装（hijack 防线）

@worker_process_init.connect
def _on_worker_process_init(**_kw):
    # 批26-11 附带根修：fork 继承的池连接非 fork-safe（共享 backend session 的 prepared
    # statement 撞名 → log_sink 整批 drop，行为冒烟实证）——先丢弃继承连接再装 sink
    try:
        from src.data_platform.db import dispose_fork_inherited_connections
        dispose_fork_inherited_connections()
    except Exception:
        pass   # 极早期窗口（db 模块未就绪）静默——sink flush 时新连接自建即净
    from src.data_platform.log_sink import install_worker
    install_worker()   # fork 后每子进程重启 flush 线程（source 取 env，systemd 单元 Environment= 各自注入）
    # 批26-11（C6 根治）：自定义因子加载从模块 import 期挪进本信号——原位在 import 期 exec 用户
    # 因子代码，凡 import celery app 的进程（beat/worker 父进程）启动即被拉起重库且进程终身不卸
    # （批9 lazy 守门只盖我方模块，盖不住 exec 的用户代码）。挪 prefork 子进程后：beat 只调度
    # 零因子、worker 父进程干净、每子进程各自加载（定义期 def 毫秒级，max-tasks-per-child 回收
    # 重付可忽略）；任务头 lazy 重载（tasks.py R-S4）已是运行期兜底。try/except 必留——
    # 信号处理器抛异常会阻断 prefork 子进程启动。
    try:
        import logging
        from src.strategy_framework.factor import load_factors_from_db
        _loaded_f = load_factors_from_db()
        if _loaded_f:
            # 行为冒烟实证：print 在子进程 init 时刻 stdout 重定向尚未接管（journal 盲区），
            # logger 走 log_sink→system_log 恒可观测（批26-11）
            logging.getLogger(__name__).info("加载自定义因子(worker 子进程): %s", ", ".join(_loaded_f))
    except Exception:
        pass   # 表未建/DB 未就绪的子进程早期窗口静默（任务头 R-S4 兜底）

@worker_shutdown.connect
def _on_worker_shutdown(**_kw):
    from src.data_platform.log_sink import sink
    if (_s := sink()) is not None:
        _s.close()   # 父进程冲刷（WorkController.stop 派发）


@worker_process_shutdown.connect
def _on_worker_process_shutdown(**_kw):
    # 盲审 A-P1-4：子进程经 billiard os._exit 退出（atexit 不跑），worker_shutdown 又只在父进程派发——
    # 本信号（子进程退出前）才是子进程尾窗冲刷点（email/im/sms 事件恰产生于 risk 子进程；max-tasks-per-child 回收同经此）
    from src.data_platform.log_sink import sink
    if (_s := sink()) is not None:
        _s.close()

# 批 7 告警三队列（显式全名映射——生产者 send_task 按名投递，此处兜路由）：
# alerts_* 由 quant-celery-risk@ 专属消费（-c 1，与主 worker data/analysis 长任务隔离，B2-P5）
app.conf.task_routes = {
    "alerts.send_im": {"queue": "alerts_im"},
    "alerts.send_email": {"queue": "alerts_email"},
    "alerts.send_sms": {"queue": "alerts_sms"},
}

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
        "astock-select-daily": {
            "task": "src.scheduler.tasks.astock_select_daily",
            "schedule": crontab(hour=16, minute=8),   # 批27-31：盘后锚（避 16:30 daily-report）
            "options": {"queue": "analysis"},
        },
        "data-increment-crypto": {
            "task": "src.scheduler.tasks.data_increment_crypto",
            "schedule": crontab(minute="*/15"),   # 批27-31：15min 无损表达
            "options": {"queue": "data"},
        },
        "sync-scheduler": {
            "task": "src.scheduler.tasks.data_sync_scheduler",
            "schedule": 300.0,   # U-4: 300s 才对得上 cron 窗口（08:45 等分钟级 schedule）
            "options": {"queue": "data"},
        },
        "data-continuity": {
            "task": "src.scheduler.tasks.data_continuity_check",
            "schedule": crontab(minute=17),   # 批27-31：每小时 :17 错峰
            "options": {"queue": "data"},
        },
        "reconcile": {
            "task": "src.scheduler.tasks.reconcile_three_books",
            "schedule": crontab(minute=43),   # 批27-31：每小时 :43（与 continuity 错开）
            "options": {"queue": "risk"},
        },
        "drift-check": {
            "task": "src.scheduler.tasks.drift_check",
            "schedule": crontab(hour=15, minute=37),   # 批27-31：盘后窗内（依赖当日分析）
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
            "schedule": crontab(hour=3, minute=13),   # 批27-31：低峰（避 04:00 备份）
            "options": {"queue": "risk"},
        },
        "logs-cleanup": {   # 批25：system_log>30d 清理
            "task": "src.scheduler.tasks.cleanup_logs",
            "schedule": crontab(hour=3, minute=23),   # 批27-31：低峰（避 04:00 备份/04:07 校准）
            "options": {"queue": "risk"},
        },
        "convertible-terms-sync": {
            "task": "src.scheduler.tasks.convertible_terms_sync",
            "schedule": crontab(hour=3, minute=43),   # 批27-31：低峰
            "options": {"queue": "data"},
        },
        "static-list-sync": {
            "task": "src.scheduler.tasks.static_list_sync",
            "schedule": crontab(day_of_week=0, hour=4, minute=37),   # 批27-31：周日低峰（避 04:07 校准）
            "options": {"queue": "data"},
        },
        "daily-report": {
            "task": "src.scheduler.tasks.daily_report",
            "schedule": crontab(hour=16, minute=30),
            "options": {"queue": "risk"},
        },
        "broker-health-check": {
            "task": "src.scheduler.tasks.broker_health_check",
            "schedule": crontab(hour="*/6", minute=51),   # 批27-31：6h 周期+错峰分钟
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
        # 腾讯分钟攒（分钟数据源重构 21 号 §3.2：每天收盘后 15:20 取一次腾讯 1min，
        # 攒进 bar_1min 供回测/研判/暖机；数据源开关 minute_data_source='tencent' 才跑）
        "tencent-minute-sync": {
            "task": "src.scheduler.tasks.tencent_minute_sync_task",
            "schedule": crontab(hour=15, minute=20),
            "options": {"queue": "data", "expires": 350},
        },
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