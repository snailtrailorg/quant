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
    "bad_target"}
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


def broadcast(category: str, title: str, body: str) -> None:
    """批39 B-P2-7：报告类广播——订阅行（category 匹配+enabled）全推，**跳过 min_level 门槛**
    （报告=用户明确订阅的常规通知非告警——info 级被 warn 门槛滤掉违背订阅意图；用户裁定
    盘后报告改走订阅链）。通道勾选/节流/配额照常；站内记录由 notify() 承担（notif_id=None
    =无审计行回写跳过——报告在 notifications 表已有 info 行）。"""
    _submit("info", category, title, body, None, None, skip_level=True)


def _submit(level: str, category: str, title: str, body: str,
            code: str | None, notif_id: int | None, skip_level: bool = False) -> None:
    global _worker_started
    if not _worker_started:
        import threading
        threading.Thread(target=_worker, daemon=True, name="alert-dispatch").start()
        _worker_started = True
    _q.put((level, category, title, body, code, notif_id, skip_level))


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
        os.environ.get("VALKEY_URL", "redis://127.0.0.1:6379/0"), decode_responses=True, socket_timeout=2, socket_connect_timeout=2)


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
            # 参数显式 ::text——psycopg3 无类型注解时 PG 对 jsonb_build_object 报
            # "could not determine data type of parameter $1"（2026-09-02 生产实证:
            # 发送链 fail-open 照常,审计回写全灭——mock 测不出,真库回归已补）
            sql = ("UPDATE notifications SET dispatch = COALESCE(dispatch,'{}'::jsonb) "
                   "|| jsonb_build_object(%s::text, %s::text) WHERE id = %s")
            params: list = [ch, value, notif_id]
            if guarded:
                sql += " AND COALESCE(dispatch->>%s::text, '') !~ '^(ok|failed:|skip:|sending)'"   # 补审E-5:sending 亦终态前置
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
                "|| jsonb_build_object(%s::text, 'sending') "
                "WHERE id = %s AND COALESCE(dispatch->>%s::text, '') = 'queued'",
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

def _throttled(ch: str, title: str, row_id: int | None = None) -> bool:
    """SET NX EX 原子节流（返回 True=15min 内已派过）。Valkey 故障 fail-open。
    多目标：键含 row_id——同通道多行各自节流（不含则同通道第二行必被首行误杀）。"""
    try:
        rk = f":{row_id}" if row_id is not None else ""
        key = f"alert:throttle:{ch}{rk}:{hashlib.md5(title.encode()).hexdigest()[:12]}"
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


def _send_im(bot_id: str, *, level: str, title: str, body: str, code: str | None) -> tuple[bool, str]:
    """IM 通道：target=bot_id，收件人=该 bot enabled 绑定用户全体（open_id 去重）。
    全成=ok；任一败=im_partial（A3-F9）。provider 限 feishu（B2-16，接第二家 IM 时扩展）。"""
    try:
        try:
            bid = int(bot_id)   # 补审E-7 意图（盲审A 范围外P0 实锤：原写 `bid = bid` 必
            # UnboundLocalError 被兜底 except 吃成 (False,"timeout")——自 db5fd27 起 IM 告警
            # 通道整体静默失效，测试 mock _send_one 故全绿）
        except (TypeError, ValueError):
            return False, "bad_target"   # 脏 target 明确失败（原在 with 内错标 timeout）
        from src.data_platform.db import get_conn
        with get_conn() as conn:
            cur = conn.execute("SELECT provider FROM im_bot_config WHERE id=%s AND enabled", (bid,))
            row = cur.fetchone()
            if not row:
                return False, "disabled"
            provider_name = row[0]
            # 批11C（A-P1-4）：只发绑定用户——首见留痕行（user_id NULL）不再收告警
            # （批30 盲审 B-P0：原注释嵌在 SQL 字符串内→psycopg SyntaxError 被吞成 (False,"timeout")
            #  ——IM 告警自批11C 上产起整体静默失效；注释移出字符串，SQL 本体零变化）
            cur = conn.execute("SELECT im_user_id FROM im_bot_users WHERE bot_id=%s AND user_id IS NOT NULL", (bid,))
            users = list({r[0] for r in cur.fetchall()})
        if not users:
            # arch-19 双轨收尾（2026-09-02）：表空则尝试 env 授权层一次性回填（扫码时代 open_id 在 env，
            # 聊天一直靠 check_user 兜底——dispatch 与聊天路径应同源）。
            # 批13（B-P2-6）：回填移到 provider 检查后——否则钉钉/企微 bot 的告警目标会把飞书
            # env 用户回填进该 bot 的 im_bot_users（跨平台污染行）。env 层本就飞书专属。
            if provider_name != "feishu":
                logger.warning("alert im dispatch: provider %s not supported yet (bot %s)", provider_name, bid)
                return False, "not_configured"
            from src.im_bot.users import backfill_from_env, list_users
            if backfill_from_env(bid) > 0:
                users = list({u["im_user_id"] for u in list_users(bid)})
        if not users:
            return False, "no_binding"
        if provider_name != "feishu":
            logger.warning("alert im dispatch: provider %s not supported yet (bot %s)", provider_name, bid)
            return False, "not_configured"
        from src.im_bot.base import get_im_provider
        provider = get_im_provider("feishu")
        if not provider:
            return False, "not_configured"
        text = f"[{level}] {title}\n{_compose(body, code, 2000)}"
        partial = False
        for uid in users:
            try:
                if not provider.send_text(bid, uid, "open_id", text):
                    partial = True
            except Exception as e:
                logger.warning("im send to %s failed: %s", uid, e)
                partial = True
        return (False, "im_partial") if partial else (True, "ok")
    except Exception as e:
        logger.error("im dispatch failed: %s", e)
        return False, "timeout"


def _send_email(to: str, *, level: str, category: str, title: str, body: str, code: str | None) -> tuple[bool, str]:
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


def _send_sms(phone: str, *, level: str, title: str) -> tuple[bool, str]:
    """短信传输层（纯发）——payload 由 _render_sms 渲染层产出（截断不再在此）。"""
    from src.alert_notify.sms import send_sms
    return send_sms(phone, level, title)


# ── 通道内容适配层（批40 Channel Profile 注册表——用户裁定建框架）──
# 通知上下文 ctx = {level, category, title, body, code, notif_id}（完整信息全通道同源）；
# 每通道一张画像：内容形态声明 + 渲染策略（ctx→payload）+ 传输函数（纯发不问内容）。
# 加新通道 = 注册表加一条（渲染+传输各一个函数），_send_one 与调用方零改动。
#
# 内容形态（content_model）：
#   full      = 全量直通（body 完整送达）——im/email
#   templated = 模板化降维（短信=敲门通知媒介，用户裁定——只传模板变量，
#               完整信息走 Web 登录或 AI 通道问答；title 截 20 字与阿里云模板 ${title} 对齐）
from dataclasses import dataclass
from typing import Any, Callable


@dataclass(frozen=True)
class ChannelProfile:
    channel: str
    content_model: str                      # "full" | "templated"
    supports_body: bool                     # 通道是否消费 body
    max_title: int | None                   # 标题截断上限（None=不截）
    renderer: Callable[[dict], dict]        # ctx → 该通道 payload
    sender: Callable[..., tuple[bool, str]] # (target, payload) → (ok, reason_token)


def _render_full(ctx: dict) -> dict:
    """im 渲染：全量直通（body 完整）。"""
    return {"level": ctx["level"], "title": ctx["title"], "body": ctx["body"], "code": ctx["code"]}


def _render_email(ctx: dict) -> dict:
    """email 渲染：全量+类别（sender 组装 html 与 runbook 行——传输层职责）。"""
    return {"level": ctx["level"], "category": ctx["category"], "title": ctx["title"],
            "body": ctx["body"], "code": ctx["code"]}


def _render_sms(ctx: dict) -> dict:
    """短信渲染（产品裁定显式契约）：降维为阿里云模板变量 {level, title[:20]}——
    丢 category/body/code；截断在渲染层（原藏 sms.py 传输层，批40 归位）。"""
    return {"level": ctx["level"], "title": str(ctx["title"])[:20]}


CHANNEL_PROFILES: dict[str, ChannelProfile] = {
    "im":    ChannelProfile("im", "full", True, None, _render_full, _send_im),
    "email": ChannelProfile("email", "full", True, None, _render_email, _send_email),
    "sms":   ChannelProfile("sms", "templated", False, 20, _render_sms, _send_sms),
}


def _send_one(row: dict, level: str, category: str, title: str, body: str, code: str | None) -> tuple[bool, str]:
    """统一入口：ctx 组装 → 画像渲染 → 传输。未知通道拒（not_configured——注册表单一真相源）。"""
    prof = CHANNEL_PROFILES.get(row["channel"])
    if prof is None:
        return False, "not_configured"
    ctx = {"level": level, "category": category, "title": title, "body": body,
           "code": code}
    payload = prof.renderer(ctx)
    return prof.sender(row["target"], **payload)


# ── 订阅加载与主流程 ──

def _ch_ok(sel, key: str) -> bool:
    """批34：订阅勾选三态判定——None=全通道；非空=按勾选；[] =零通道静音。
    禁 `not sel` 写法：[] 必须零通道不能假值全开（方案盲审 A-P1-1/B-P2-4 双抓）。
    落本模块导出=test 端点同源 import，两份谓词防漂移（盲审 A-P2-3）。"""
    return sel is None or key in sel


def _load_channels() -> list[dict]:
    """批30：订阅用户展开为通道目标行（函数名保留——dispatch 机制测试的 patch 缝）。

    行结构契约（方案 30-3）：{channel, target, categories, min_level, id, sub_id}——
    email/sms 行 id=user_id、im 行 id=bot_id（(ch,id) 全局唯一：节流/dkey/claim 三链防撞
    ——盲审 A-P0-3/B-P1-2）；sub_id=alert_user_sub 行 id（worker 侧重查用）。
    软删/停用用户 JOIN 过滤=自然出列表（用户裁定：删用户自动退出通知）；通道不可用跳过
    （手机无/短信凭证未配；邮箱仅有值判定——发送侧 outbox 兜底失败可见，跳过反而静默）。
    DB 异常 = 空 + warn（订阅在 DB，DB 故障=外推不可用，与旧 webhook 同语义）。"""
    try:
        from src.data_platform.db import get_conn
        from src.alert_notify.sms import sms_configured as _sms_ok
        rows: list[dict] = []
        with get_conn() as conn:
            subs = conn.execute(
                "SELECT s.id, s.user_id, s.categories, s.min_level, s.channels, u.email, u.phone "
                "FROM alert_user_sub s JOIN users u ON u.id=s.user_id "
                "AND u.enabled AND u.deleted_at IS NULL WHERE s.enabled ORDER BY s.id").fetchall()
            bots = conn.execute(
                "SELECT owner_user_id, id FROM im_bot_config WHERE enabled "
                "AND owner_user_id IS NOT NULL ORDER BY id").fetchall()
        bots_by_user: dict[int, list[int]] = {}
        for owner, bid in bots:
            bots_by_user.setdefault(owner, []).append(bid)
        sms_ok = _sms_ok()
        for sid, uid, cats, min_level, sel, email, phone in subs:
            if email and _ch_ok(sel, "email"):
                rows.append({"id": uid, "sub_id": sid, "channel": "email", "target": email,
                             "categories": cats or [], "min_level": min_level})
            if phone and _ch_ok(sel, "sms"):
                if sms_ok:
                    rows.append({"id": uid, "sub_id": sid, "channel": "sms", "target": phone,
                                 "categories": cats or [], "min_level": min_level})
                else:
                    logger.info("用户 %s 手机通道跳过（短信凭证未配）", uid)
            for bid in bots_by_user.get(uid, []):   # 用户裁定：名下 bot 全发（批34：勾选了特定 bot 则按勾选）
                if _ch_ok(sel, f"im:{bid}"):
                    rows.append({"id": bid, "sub_id": sid, "channel": "im", "target": str(bid),
                                 "categories": cats or [], "min_level": min_level})
        return rows
    except Exception as e:
        logger.warning("load alert_user_sub failed: %s", e)
        return []


def _dispatch_async(level: str, category: str, title: str, body: str,
                    code: str | None, notif_id: int | None) -> None:
    rows = _load_channels()
    if not rows:
        # 批30（盲审 A-P1-2 裁定）：零订阅=不外推——旧"legacy webhook 外推"过渡兜底随订阅
        # 用户化退役（新表空环境重燃已失效 webhook；审计链经 _writeback_empty 不断）
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
        dkey = f"{ch}:{row.get('id', '')}"   # 多目标：每行独立审计/节流键（ch 键同行互撞）
        if _throttled(ch, title, row.get("id")):
            _writeback(notif_id, dkey, "skip:throttled")
            continue
        if _quota_exceeded(ch):
            _writeback(notif_id, dkey, "skip:quota")
            continue
        _writeback(notif_id, dkey, "queued")          # 先写 queued 再投（A3-F3）+ 终态守卫（B3-1②）双保险
        payload = {"row": row, "dkey": dkey, "level": level, "category": category,
                   "title": str(title)[:500], "body": str(body)[:4000],
                   "code": code, "notif_id": notif_id}
        try:
            _get_producer().send_task(f"alerts.send_{ch}", queue=f"alerts_{ch}",
                                      kwargs=payload, expires=3600, retry=False)
        except Exception as e:
            logger.warning("enqueue alerts_%s failed (degrade to direct send): %s", ch, e)
            try:
                # B 评 P2-4：claim 认领（与 worker 竞争唯一发送权；worker 早完成则此处弃发）
                if not _claim(notif_id, dkey):
                    continue
                ok, reason = _send_one(row, level, category, title, body, code)   # 降级直发（D-F1）
                _writeback(notif_id, dkey, "ok" if ok else f"failed:{reason}")
            except Exception as e2:
                # 异常隔离（⑦）：单通道降级直发抛不反噬其他通道
                logger.error("direct send (%s) failed: %s", dkey, e2)
                _writeback(notif_id, dkey, "failed:timeout")


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
