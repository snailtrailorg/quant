"""告警订阅分发引擎（批 7 · 2026-09-02，docs/任务/批7-告警订阅分发.md）。

notify() 的外推新主路径：进程内单 daemon 工作线程+队列（排队串行，零新服务）→
订阅过滤 → 原子节流/配额 → Celery 三队列（alerts_im/email/sms，risk worker 消费）→
broker 故障降级线程直发（D-F1：告警不能与被监控对象共死）。

全程可审计（用户终裁）：每通道投递结局回写 notifications.dispatch jsonb——
ok / queued / failed:<token> / skip:<token> / legacy / _chain；{}=链跑完零外推；
null=未跑完（死亡窗）。reason 只允许稳定枚举 token，异常原文只进 journal。

零阻塞承诺：notify() 同步增量 = 一次 queue.put（µs 级）。真实网络发送
全部发生在 risk worker 进程或降级时的本工作线程——业务路径不感知。
"""
from __future__ import annotations

import hashlib
import logging
import os
from datetime import datetime
from zoneinfo import ZoneInfo

import redis

logger = logging.getLogger("alert_dispatch")

_LEVEL_RANK = {"info": 0, "warn": 1, "critical": 2}
# reason 稳定枚举（A3-F1/B3-5：str(e) 原文可能含收件人/手机号/URL，禁入审计列）
_REASON_TOKENS = {
    "throttled", "quota", "disabled", "timeout", "smtp_refused", "smtp_error",
    "enqueue", "submit", "im_partial", "no_binding", "not_configured", "expired",
}
_LIMITS = {"im": 100, "email": 100, "sms": int(os.environ.get("ALERT_SMS_DAILY_QUOTA", "20"))}
_TZ = ZoneInfo("Asia/Shanghai")

# ── 进程内单工作线程：同进程多告警排队串行；队列有意 unbounded ──
# （maxsize+丢弃 = 静默丢告警破审计承诺；风暴积压 = 延迟非丢失，B3/F8 成文）
# 实现：daemon Thread + queue.Queue——ThreadPoolExecutor 在 Py3.9+ 为非守护线程，
# 解释器退出会 join 并排空队列（live-task stop 被在途发送阻塞超时遭 SIGKILL，A 评 P2-5）；
# daemon 线程随进程生灭，符合"零阻塞业务/进程退出不等待"契约。
import queue as _queue
_q: "_queue.Queue" = _queue.Queue()
_worker_started = False


def _worker() -> None:
    while True:
        args = _q.get()
        try:
            _dispatch_async(*args)
        except Exception as e:
            logger.error("alert_dispatch chain failed: %s", e)
        finally:
            _q.task_done()


def _submit(level: str, category: str, title: str, body: str,
            code: str | None, notif_id: int | None) -> None:
    global _worker_started
    if not _worker_started:
        import threading
        threading.Thread(target=_worker, daemon=True, name="alert-dispatch").start()
        _worker_started = True
    _q.put((level, category, title, body, code, notif_id))


# ── broker-only 生产者：模块级惰性单例（仅 executor 单线程使用，免锁）──
# 零 import scheduler.app/tasks（B2-P7：其模块级 DB 读/因子加载/schema 校验不进
# live-task 首告警热路径）；3s 建连/5s socket 快失败 + retry=False → 秒级落降级直发（B3-6）
_producer = None


def _get_producer():
    global _producer
    if _producer is None:
        from celery import Celery
        broker = os.environ.get("CELERY_BROKER_URL") or os.environ.get("VALKEY_URL", "redis://127.0.0.1:6379/0")
        _producer = Celery("quant", broker=broker,
                           broker_transport_options={"socket_connect_timeout": 3, "socket_timeout": 5})
    return _producer


def _redis() -> redis.Redis:
    return redis.Redis.from_url(
        os.environ.get("VALKEY_URL", "redis://127.0.0.1:6379/0"), decode_responses=True)


# ── 回写契约（A3-F2/F3/F4/F5，B3-1/10 收口）──

def _writeback(notif_id: int | None, ch: str, value: str) -> None:
    """投递结局并入 notifications.dispatch。单语句原子合并，禁 read-modify-write。
    queued 写挂终态守卫（迟到的 queued 永不覆盖 worker 已落的 ok/failed/skip）；
    自身失败必须 log error——审计失败不可静默。"""
    if not notif_id:
        return   # 站内 insert 失败/无 id：外推照跑，回写跳过（A3-F5）
    # reason 软校验（A 评 P2-7）：值域外记 warning——枚举宣称不能只靠注释
    _v = value.split(":", 1)
    if not (value in ("ok", "queued", "legacy")
            or (len(_v) == 2 and _v[0] in ("failed", "skip")
                and (_v[1] in _REASON_TOKENS or _v[1].startswith("ALIYUN_")))):
        logger.warning("writeback non-token value nid=%s ch=%s val=%s", notif_id, ch, value)
    guarded = value == "queued"
    try:
        from src.data_platform.db import get_conn
        with get_conn() as conn:
            sql = ("UPDATE notifications SET dispatch = COALESCE(dispatch,'{}'::jsonb) "
                   "|| jsonb_build_object(%s, %s) WHERE id = %s")
            params: list = [ch, value, notif_id]
            if guarded:
                sql += " AND COALESCE(dispatch->>%s, '') !~ '^(ok|failed:|skip:)'"
                params.append(ch)
            conn.execute(sql, tuple(params))
            conn.commit()
    except Exception as e:
        logger.error("alert_dispatch writeback failed nid=%s ch=%s val=%s: %s", notif_id, ch, value, e)


def _claim(notif_id: int | None, ch: str) -> bool:
    """认领发送权（B 评 P2-4：send_task 超时-after-accept 双发窗，短信=双倍计费）。
    queued→sending 单向迁移，rowcount=1 才获得发送权——降级直发与 worker 任务
    只有一方能认领成功。sending 悬置=worker 死亡窗（audit 可见，known-limit）。"""
    if not notif_id:
        return True   # 无审计行（站内 insert 失败）不拦发送
    try:
        from src.data_platform.db import get_conn
        with get_conn() as conn:
            cur = conn.execute(
                "UPDATE notifications SET dispatch = COALESCE(dispatch,'{}'::jsonb) "
                "|| jsonb_build_object(%s, 'sending') "
                "WHERE id = %s AND COALESCE(dispatch->>%s, '') = 'queued'",
                (ch, notif_id, ch))
            conn.commit()
            return cur.rowcount == 1
    except Exception as e:
        logger.error("alert_dispatch claim failed nid=%s ch=%s: %s（放行——审计故障不拦发送）", notif_id, ch, e)
        return True


def _writeback_empty(notif_id: int | None) -> None:
    """链跑完但零通道匹配 → 写 {}（终态标记；与 null=未跑完 拆分，B3-2 null 三义）。"""
    if not notif_id:
        return
    try:
        from src.data_platform.db import get_conn
        with get_conn() as conn:
            conn.execute(
                "UPDATE notifications SET dispatch = '{}'::jsonb "
                "WHERE id = %s AND dispatch IS NULL", (notif_id,))
            conn.commit()
    except Exception as e:
        logger.error("alert_dispatch writeback(empty) failed nid=%s: %s", notif_id, e)


# ── 节流/配额（原子原语，fail-open）──

def _throttled(ch: str, title: str) -> bool:
    """SET NX EX 原子节流（返回 True=15min 内已派过）。Valkey 故障 fail-open。"""
    try:
        key = f"alert:throttle:{ch}:{hashlib.md5(title.encode()).hexdigest()[:12]}"
        return not _redis().set(key, "1", nx=True, ex=900)
    except Exception as e:
        logger.warning("throttle key unavailable (fail-open): %s", e)
        return False


def _quota_exceeded(ch: str) -> bool:
    """先 INCR 后比限（原子，不可超发）。键名滚日=自然日重置；EXPIRE 仅 GC。fail-open。"""
    try:
        r = _redis()
        k = f"alert:quota:{ch}:{datetime.now(_TZ).strftime('%Y%m%d')}"
        n = r.incr(k)
        if n == 1:
            r.expire(k, 86400)
        return n > _LIMITS.get(ch, 100)
    except Exception as e:
        logger.warning("quota check failed (fail-open): %s", e)
        return False


# ── 三通道 sender（降级直发与 worker 任务共用；返回 (ok, reason_token)）──

def _runbook_line(code: str | None) -> str:
    if not code:
        return ""
    try:
        from src.alert_notify.runbook import RUNBOOK
        rb = RUNBOOK.get(code)
        return f"\n▸ 处置[{rb['label']}]: {rb['guide']}" if rb else ""
    except Exception:
        return ""


def _compose(body: str, code: str | None, limit: int) -> str:
    """先截原 body 再拼 runbook 行——处置行永不落截断区（W3 纪律移植）。"""
    line = _runbook_line(code)
    return str(body)[:max(limit - len(line), 0)] + line


def _send_im(bot_id: str, level: str, title: str, body: str, code: str | None) -> tuple[bool, str]:
    """IM 通道：target=bot_id，收件人=该 bot enabled 绑定用户全体（open_id 去重）。
    全成=ok；任一败=im_partial（A3-F9）。provider 限 feishu（B2-16，接第二家 IM 时扩展）。"""
    try:
        from src.data_platform.db import get_conn
        with get_conn() as conn:
            cur = conn.execute("SELECT provider FROM im_bot_config WHERE id=%s AND enabled", (int(bot_id),))
            row = cur.fetchone()
            if not row:
                return False, "disabled"
            provider_name = row[0]
            cur = conn.execute("SELECT im_user_id FROM im_bot_users WHERE bot_id=%s", (int(bot_id),))
            users = list({r[0] for r in cur.fetchall()})
        if not users:
            # 19 号双轨收尾（2026-09-02）：表空则尝试 env 授权层一次性回填（扫码时代 open_id 在 env，
            # 聊天一直靠 check_user 兜底——dispatch 与聊天路径应同源）
            from src.im_bot.users import backfill_from_env, list_users
            if backfill_from_env(int(bot_id)) > 0:
                users = list({u["im_user_id"] for u in list_users(int(bot_id))})
        if not users:
            return False, "no_binding"
        if provider_name != "feishu":
            logger.warning("alert im dispatch: provider %s not supported yet (bot %s)", provider_name, bot_id)
            return False, "not_configured"
        from src.im_bot.base import get_im_provider
        provider = get_im_provider("feishu")
        if not provider:
            return False, "not_configured"
        text = f"[{level}] {title}\n{_compose(body, code, 2000)}"
        partial = False
        for uid in users:
            try:
                if not provider.send_text(int(bot_id), uid, "open_id", text):
                    partial = True
            except Exception as e:
                logger.warning("im send to %s failed: %s", uid, e)
                partial = True
        return (False, "im_partial") if partial else (True, "ok")
    except Exception as e:
        logger.error("im dispatch failed: %s", e)
        return False, "timeout"


def _send_email(to: str, level: str, category: str, title: str, body: str, code: str | None) -> tuple[bool, str]:
    """邮件通道：入 outbox（持久+重试+终败 email.failed 回流站内）后立即同步试发（B-P9 时效）。"""
    try:
        from src.email_service import queue_email
        from src.email_service import _try_row_sync
        import html as _html
        subject = f"[{level}][{category}] {title}"
        html_body = f"<pre style=\"font-family:ui-monospace,monospace\">{_html.escape(_compose(body, code, 4000))}</pre>"
        outbox_id = queue_email(to, subject, html_body)
        _try_row_sync(outbox_id)   # 立即试发（失败由 outbox 退避+beat sweep 兜底）
        return True, "ok"          # ok=已入 outbox 链（其终败自有站内回流与页面可观测）
    except Exception as e:
        logger.error("email dispatch failed: %s", e)
        return False, "smtp_error"


def _send_sms(phone: str, level: str, title: str) -> tuple[bool, str]:
    from src.alert_notify.sms import send_sms
    return send_sms(phone, level, title)


def _send_one(row: dict, level: str, category: str, title: str, body: str, code: str | None) -> tuple[bool, str]:
    ch = row["channel"]
    if ch == "im":
        return _send_im(row["target"], level, title, body, code)
    if ch == "email":
        return _send_email(row["target"], level, category, title, body, code)
    return _send_sms(row["target"], level, title)


# ── 订阅加载与主流程 ──

def _load_channels() -> list[dict]:
    """enabled 订阅行。DB 异常 = 空 + warn（订阅在 DB，DB 故障=外推不可用，与旧 webhook 同语义）。"""
    try:
        from src.data_platform.db import get_conn
        with get_conn() as conn:
            cur = conn.execute(
                "SELECT channel, target, categories, min_level FROM alert_channel_sub WHERE enabled")
            return [{"channel": r[0], "target": r[1], "categories": r[2] or [],
                     "min_level": r[3]} for r in cur.fetchall()]
    except Exception as e:
        logger.warning("load alert_channel_sub failed: %s", e)
        return []


def _dispatch_async(level: str, category: str, title: str, body: str,
                    code: str | None, notif_id: int | None) -> None:
    from src.alert_notify.notify import should_push_external, _push_channel   # 惰性（防环+冷启动零增重）
    rows = _load_channels()
    if not rows:
        # 过渡兜底（A2-P4/B2-P6）：零 enabled 订阅 = 沿用旧 webhook 外推规则，部署日不黑洞；
        # 订阅配好自然失效，下个版本周期移除本分支。兜底也回写（B3-3：审计链不断）。
        if should_push_external(category, level):
            # B 评 P1：旧 notify:external 15min 节流随旧外推块删除而丢——E-4 的 60s 循环
            # critical（断流类）会 ~100min 烧光 webhook 配额。同款 NX 补回。
            if _throttled("legacy", title):
                _writeback(notif_id, "legacy", "skip:throttled")
                return
            ok = _push_channel(level, title, body, code, notif_id=notif_id)
            _writeback(notif_id, "legacy", "ok" if ok else "failed:timeout")
        else:
            _writeback_empty(notif_id)
        return
    matched = [r for r in rows
               if category in (r["categories"] or [])
               and _LEVEL_RANK.get(level, 0) >= _LEVEL_RANK.get(r["min_level"], 1)]
    if not matched:
        _writeback_empty(notif_id)
        return
    for row in matched:
        ch = row["channel"]
        if _throttled(ch, title):
            _writeback(notif_id, ch, "skip:throttled")
            continue
        if _quota_exceeded(ch):
            _writeback(notif_id, ch, "skip:quota")
            continue
        _writeback(notif_id, ch, "queued")          # 先写 queued 再投（A3-F3）+ 终态守卫（B3-1②）双保险
        payload = {"row": row, "level": level, "category": category,
                   "title": str(title)[:500], "body": str(body)[:4000],
                   "code": code, "notif_id": notif_id}
        try:
            _get_producer().send_task(f"alerts.send_{ch}", queue=f"alerts_{ch}",
                                      kwargs=payload, expires=3600, retry=False)
        except Exception as e:
            logger.warning("enqueue alerts_%s failed (degrade to direct send): %s", ch, e)
            try:
                # B 评 P2-4：claim 认领（与 worker 竞争唯一发送权；worker 早完成则此处弃发）
                if not _claim(notif_id, ch):
                    continue
                ok, reason = _send_one(row, level, category, title, body, code)   # 降级直发（D-F1）
                _writeback(notif_id, ch, "ok" if ok else f"failed:{reason}")
            except Exception as e2:
                # 异常隔离（⑦）：单通道降级直发抛不反噬其他通道
                logger.error("direct send (%s) failed: %s", ch, e2)
                _writeback(notif_id, ch, "failed:timeout")


def dispatch(level: str, category: str, title: str, body: str,
             code: str | None = None, notif_id: int | None = None) -> None:
    """notify() 外推入口（info 级在调用方已被拒）。永不 raise。"""
    if _LEVEL_RANK.get(level, 0) < 1:
        return
    if code == "alert.push-failed":
        # 防环：推送失败通知自身只留站内（否则 IM 故障时失败通知再失败→套娃放大，
        # B2-P8 的"标题各异+NX"挡不住嵌套前缀标题，闸在源头）
        _writeback_empty(notif_id)
        return
    try:
        _submit(level, category, title, body, code, notif_id)
    except Exception as e:
        logger.error("alert_dispatch submit failed: %s", e)
        _writeback(notif_id, "_chain", "failed:submit")
