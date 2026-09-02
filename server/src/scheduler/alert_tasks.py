"""告警三通道 Celery 任务（批 7 · 2026-09-02）。

模块归属：scheduler（层 3）——celery 任务定义天然属调度层，且 im_bot（层 3）调用
与 alert_notify（层 2）导入在本层均合法（原放 alert_notify/ 下两跳上行违规，
tests/test_layering 守门抓出后归位）。

队列路由（scheduler/app.py task_routes）：alerts.send_im → alerts_im /
alerts.send_email → alerts_email / alerts.send_sms → alerts_sms，由
quant-celery-risk@（-c 1 专属）消费——与主 worker 的 data/analysis 长任务隔离。

发送策略：**单次尝试**（偏离方案 v4 的 autoretry×3，实审后修正：三 sender 内部
已吞网络异常返 (False, reason)——任务层 autoretry 永不触发属死配置；且短信失败
盲重试有双发计费风险。告警是周期性事件，单条失丢由下一条天然补位，终态可查）。
发送前重查 enabled（B2-P9：入队后用户关通道，按快照发=违背意图，计费敏感）。
"""
from __future__ import annotations

import logging

logger = logging.getLogger("alert_dispatch")


def _register():
    """惰性注册（避免 alert_notify 包导入期触发 scheduler.app 的模块级 DB 读——
    仅 celery worker 启动期经 include 加载本模块时执行）。"""
    from src.scheduler.app import app
    from src.alert_notify import dispatch as D
    from src.data_platform.db import get_conn

    def _still_enabled(row: dict) -> bool:
        """行级重查（批7.1 多目标）：入队后该行被关/删则 skip（计费敏感）。"""
        try:
            with get_conn() as conn:
                cur = conn.execute(
                    "SELECT enabled FROM alert_channel_sub WHERE id=%s", (row.get("id"),))
                r = cur.fetchone()
                return bool(r and r[0])
        except Exception as e:
            logger.warning("re-check enabled(row %s) failed: %s", row.get("id"), e)
            return True   # 查不到按快照发（快照本身是入队时刻的有效订阅）

    def _finish(ch: str, row: dict, level: str, category: str, title: str,
                body: str, code, notif_id, dkey=None) -> None:
        dkey = dkey or ch   # 多目标：行级审计键（批7.1）
        if not _still_enabled(row):
            D._writeback(notif_id, dkey, "skip:disabled")
            return
        if not D._claim(notif_id, dkey):
            # B 评 P2-4：降级直发已认领（send_task 响应超时但消息已达 broker 的双发窗）
            return
        ok, reason = D._send_one(row, level, category, title, body, code)
        D._writeback(notif_id, dkey, "ok" if ok else f"failed:{reason}")
        if not ok:
            # 终败站内通知（dispatch 对 code=alert.push-failed 有防环闸，只留站内）
            try:
                from src.alert_notify.notify import safe_notify
                safe_notify("warn",
                            f"告警推送失败[{ch}]: {str(title)[:60]}",
                            f"reason={reason} level={level}", code="alert.push-failed")
            except Exception as e:
                logger.error("push-failed notify error: %s", e)

    @app.task(name="alerts.send_im", soft_time_limit=15)
    def send_im(row=None, level="", category="", title="", body="", code=None, notif_id=None, dkey=None):
        _finish("im", row or {}, level, category, title, body, code, notif_id, dkey)

    @app.task(name="alerts.send_email", soft_time_limit=70)   # B 评 P3：≥SMTP 60s,防 soft 超时炸在 _try_row_sync 内致 outbox 行卡死 sending（sweep 只扫 pending）
    def send_email(row=None, level="", category="", title="", body="", code=None, notif_id=None, dkey=None):
        _finish("email", row or {}, level, category, title, body, code, notif_id, dkey)

    @app.task(name="alerts.send_sms", soft_time_limit=15)
    def send_sms(row=None, level="", category="", title="", body="", code=None, notif_id=None, dkey=None):
        _finish("sms", row or {}, level, category, title, body, code, notif_id, dkey)

    return send_im, send_email, send_sms


send_im, send_email, send_sms = _register()
