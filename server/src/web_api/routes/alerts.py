"""Web 后端 · 告警订阅路由（批 7 · /api/alerts/*，docs/任务/批7-告警订阅分发.md）。

全局一套订阅（im/email/sms 三行）+ 短信凭证专用端点 + 逐通道测试。
权限 alerts_config（admin 专属——告警路由/计费短信面不给 analyst）。
手机号回显打码；PUT 全量替换；target 含 * = 保留库值（smtp password 先例）。
"""
from __future__ import annotations

import re

from fastapi import APIRouter, Depends, Body

from ..auth import require_perm, audit_log
from ..errors import ApiError
from ..redis_pool import redis_client as get_redis
from src.data_platform.db import get_conn

router = APIRouter(tags=["alerts"])

_CATEGORIES = ("risk", "task", "data", "system")
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_PHONE_RE = re.compile(r"^1[3-9]\d{9}$")


def _mask(channel: str, target: str | None) -> str | None:
    if not target:
        return target
    if channel == "sms" and len(target) == 11:
        return f"{target[:3]}****{target[-4:]}"
    return target


def _channels_from_db() -> list[dict]:
    with get_conn() as conn:
        cur = conn.execute(
            "SELECT channel, target, categories, min_level, enabled FROM alert_channel_sub")
        return [{"channel": r[0], "target": r[1], "categories": r[2] or [],
                 "min_level": r[3], "enabled": r[4]} for r in cur.fetchall()]


@router.get("/api/alerts/config")
def alerts_config_get(payload: dict = Depends(require_perm("alerts_config"))):
    from src.alert_notify.sms import sms_configured
    from src.alert_notify.dispatch import _LIMITS
    channels = {r["channel"]: r for r in _channels_from_db()}
    out = []
    for ch in ("im", "email", "sms"):
        r = channels.get(ch, {"channel": ch, "target": None, "categories": [],
                              "min_level": "critical" if ch == "sms" else "warn",
                              "enabled": False})
        r = dict(r)
        r["target"] = _mask(ch, r["target"])
        out.append(r)
    with get_conn() as conn:
        cur = conn.execute(
            "SELECT b.id, b.provider, b.name, b.enabled, "
            "(SELECT count(*) FROM im_bot_users u WHERE u.bot_id = b.id) AS n "
            "FROM im_bot_config b ORDER BY b.id")
        bots = [{"id": r[0], "provider": r[1], "name": r[2], "enabled": r[3],
                 "bound_users": r[4]} for r in cur.fetchall()]
    return {"channels": out, "sms_configured": sms_configured(), "im_bots": bots,
            "quota": dict(_LIMITS)}


@router.put("/api/alerts/config")
def alerts_config_put(body: dict = Body(...), payload: dict = Depends(require_perm("alerts_config"))):
    """全量替换：未出现的 channel 一律置 enabled=false；target 含 * = 保留库值（打码 sentinel）。"""
    incoming = body.get("channels", [])
    if len(incoming) > 3:
        raise ApiError(400, "BAD_REQUEST", "channels 至多 im/email/sms 三项")
    with get_conn() as conn:
        cur = conn.execute("SELECT channel, target FROM alert_channel_sub")
        stored = {r[0]: r[1] for r in cur.fetchall()}
        bot_ids: set[int] = set()
        cur = conn.execute("SELECT id FROM im_bot_config WHERE enabled")
        bot_ids = {r[0] for r in cur.fetchall()}

        seen: set[str] = set()
        for item in incoming:
            ch = item.get("channel")
            if ch not in ("im", "email", "sms"):
                raise ApiError(400, "BAD_REQUEST", f"非法通道: {ch}")
            if ch in seen:
                raise ApiError(400, "BAD_REQUEST", f"通道重复: {ch}")
            seen.add(ch)
            cats = item.get("categories", [])
            if not isinstance(cats, list) or any(c not in _CATEGORIES for c in cats):
                raise ApiError(400, "BAD_REQUEST", "categories 须为 risk/task/data/system 子集")
            if item.get("min_level", "warn") not in ("warn", "critical"):
                raise ApiError(400, "BAD_REQUEST", "min_level 须为 warn|critical")
            target = (item.get("target") or "").strip()
            enabled = bool(item.get("enabled"))
            if enabled and not target:
                raise ApiError(400, "TARGET_REQUIRED", f"{ch} 通道启用须填目标")
            if target and "*" in target:
                target = stored.get(ch) or ""   # 打码 sentinel：保留库值
                if enabled and not target:
                    raise ApiError(400, "TARGET_REQUIRED", f"{ch} 通道启用须填目标")
            if enabled and target:
                if ch == "email" and not _EMAIL_RE.match(target):
                    raise ApiError(400, "BAD_REQUEST", "邮箱格式不合法")
                if ch == "sms" and not _PHONE_RE.match(target):
                    raise ApiError(400, "BAD_REQUEST", "手机号格式不合法（仅支持中国大陆）")
                if ch == "im":
                    try:
                        bid = int(target)
                    except ValueError:
                        raise ApiError(400, "BAD_REQUEST", "IM 目标须为 bot id")
                    if bid not in bot_ids:
                        raise ApiError(400, "BAD_REQUEST", "IM 目标须为存在且启用的 bot")
            # UPSERT
            import json as _json
            conn.execute(
                "INSERT INTO alert_channel_sub (channel, target, categories, min_level, enabled, updated_at) "
                "VALUES (%s, %s, %s::jsonb, %s, %s, now()) "
                "ON CONFLICT (channel) DO UPDATE SET "
                "target=EXCLUDED.target, categories=EXCLUDED.categories, "
                "min_level=EXCLUDED.min_level, enabled=EXCLUDED.enabled, updated_at=now()",
                (ch, target or None, _json.dumps(cats), item.get("min_level", "warn"), enabled))
        # 全量替换：未出现的通道禁用
        for ch in ("im", "email", "sms"):
            if ch not in seen:
                conn.execute(
                    "UPDATE alert_channel_sub SET enabled=false, updated_at=now() WHERE channel=%s", (ch,))
        conn.commit()
    with get_conn() as conn:
        cur = conn.execute("SELECT channel, target FROM alert_channel_sub")
        new_stored = {r[0]: r[1] for r in cur.fetchall()}
    audit_log(payload["username"], "alerts_config_save",   # A 评 P2-8：审计记新值（打码）而非仅旧值
              detail="; ".join(f"{ch}: {item.get('enabled')}, {_mask(ch, new_stored.get(ch) or '')}"
                               for item in incoming for ch in [item.get("channel")]))
    return {"ok": True}


@router.get("/api/alerts/sms-config")
def sms_config_get(payload: dict = Depends(require_perm("alerts_config"))):
    """凭证状态：只回 secret_set 布尔与非密钥项（access_key_id 不回显）。"""
    from src.alert_notify.sms import _sms_config, sms_configured
    cfg = _sms_config() or {}
    return {"secret_set": bool(cfg.get("alert_sms_access_key_secret")),
            "sms_configured": sms_configured(),
            "sign_name": cfg.get("alert_sms_sign_name", ""),
            "template_code": cfg.get("alert_sms_template_code", "")}


@router.put("/api/alerts/sms-config")
def sms_config_put(body: dict = Body(...), payload: dict = Depends(require_perm("alerts_config"))):
    from src.quant_common.crypto import encrypt
    fields = {"access_key_id": "alert_sms_access_key_id",
              "access_key_secret": "alert_sms_access_key_secret",
              "sign_name": "alert_sms_sign_name",
              "template_code": "alert_sms_template_code"}
    with get_conn() as conn:
        for k, col in fields.items():
            v = (body.get(k) or "").strip()
            if not v:
                continue   # 留空=不修改（smtp 先例）
            if k == "access_key_secret":
                v = encrypt(v)
            conn.execute(
                "INSERT INTO system_config (key, value, value_type, description) "
                f"VALUES ('{col}', %s, {'%r' % ('password' if k == 'access_key_secret' else 'text')}, '阿里云短信凭证（批7）') "
                "ON CONFLICT (key) DO UPDATE SET value=EXCLUDED.value",
                (v,))
        conn.commit()
    audit_log(payload["username"], "alerts_sms_config_save",
              detail=f"keys={[k for k in fields if (body.get(k) or '').strip()]}")
    return {"ok": True}


@router.post("/api/alerts/test")
def alerts_test(body: dict = Body(...), payload: dict = Depends(require_perm("alerts_config"))):
    """逐通道测试发送（绕节流/配额）；per-actor 60s 冷却；结果落 code=alert.test 站内通知。"""
    ch = body.get("channel")
    if ch not in ("im", "email", "sms"):
        raise ApiError(400, "BAD_REQUEST", f"非法通道: {ch}")
    actor = payload["username"]
    r = get_redis()
    cool = f"alert:test:cooldown:{actor}:{ch}"
    if r.exists(cool):
        raise ApiError(429, "TOO_MANY_REQUESTS", "测试冷却中（60s/通道）")
    r.setex(cool, 60, "1")

    rows = {x["channel"]: x for x in _channels_from_db()}
    row = rows.get(ch)
    detail, ok = "", False
    if ch == "im":
        from src.im_bot.base import get_im_provider
        if not row or not row.get("target"):
            detail, ok = "通道未配置", False
        else:
            with get_conn() as conn:
                cur = conn.execute("SELECT provider FROM im_bot_config WHERE id=%s AND enabled",
                                   (int(row["target"]),))
                b = cur.fetchone()
                cur = conn.execute("SELECT count(*) FROM im_bot_users WHERE bot_id=%s",
                                   (int(row["target"]),))
                n_bound = cur.fetchone()[0]
            if not b:
                detail, ok = "bot 不存在或未启用", False
            elif n_bound == 0:
                detail, ok = "无绑定用户（先在飞书对 bot 发送任意消息完成绑定）", False
            else:
                # 批 8.5（2026-09-02）：send_text 已真实 bool（批 7 改造），测试升级为
                # test_connection + 真发一条到全体绑定用户（email/sms 同款真发语义，e2e 闭环）
                p = get_im_provider(b[0])
                if not p:
                    ok, detail = False, "provider 缺失"
                else:
                    ok, detail = p.test_connection(int(row["target"]))
                    if ok:
                        with get_conn() as conn:
                            cur = conn.execute("SELECT im_user_id FROM im_bot_users WHERE bot_id=%s",
                                               (int(row["target"]),))
                            bound = [r[0] for r in cur.fetchall()]
                        sent = sum(1 for u in bound
                                   if p.send_text(int(row["target"]), u, "open_id", "[test] 告警通道测试——收到本条即 IM 告警链路已通"))
                        detail = f"连接正常，测试消息已发 {sent}/{len(bound)} 位绑定用户" if sent else "连接正常但 0 人送达（查绑定/发送日志）"
                        ok = sent > 0
                    else:
                        detail = str(detail)[:80] or "连接失败"
    elif ch == "email":
        # B 评 P3：只入队不同步试发——SMTP 60s > axios 30s，同步会让前端超时；
        # 发送由 beat sweep（60s 周期）兜，outbox 页可查（测试通知 code=alert.test 同步可见）
        from src.email_service import queue_email
        if not row or not row.get("target"):
            detail, ok = "通道未配置", False
        else:
            try:
                queue_email(row["target"], "[test] 告警通道测试",
                            "<pre>这是一封测试邮件（设置→告警→测试）</pre>")
                ok, detail = True, "已入发送队列（outbox 60s 内发出，发送记录页可查）"
            except Exception:
                ok, detail = False, "发送失败: smtp_error"
    else:
        from src.alert_notify.sms import send_sms, sms_configured
        if not sms_configured():
            detail, ok = "短信未接入（API key 未申请，凭证到位即通）", False
        elif not row or not row.get("target"):
            detail, ok = "通道未配置手机号", False
        else:
            ok, reason = send_sms(row["target"], "info", "告警通道测试")
            detail = "已发送" if ok else f"发送失败: {reason}"

    try:
        from src.alert_notify.notify import notify
        notify("info", "system", f"告警测试[{ch}] · {'成功' if ok else '失败'}",
              f"{detail}（by {actor}）", code="alert.test")
    except Exception as e:
        import logging
        logging.getLogger("web_api").warning("alert.test notify failed: %s", e)
    audit_log(payload["username"], "alerts_test", detail=f"channel={ch} ok={ok}")   # A 评 P2-4:计费面必留痕
    return {"ok": ok, "detail": detail}
