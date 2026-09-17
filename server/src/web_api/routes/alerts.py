"""Web 后端 · 告警订阅路由（批 7 起；批30 订阅用户化——docs/任务/批30-通知用户化与手机号.md）。

订阅 CRUD（维度=**用户**：被选用户的邮箱/手机/名下 bot 即投递通道，dispatch 展开裁量——
可用就发不可用跳过，软删用户 JOIN 过滤=自然出列表）+ 短信凭证端点（含批30 验证码模板键）
+ 用户维度测试。权限 alerts_config（admin 专属——告警路由/计费短信面不给 analyst）。
手机号回显打码（legacy 清单同走 _mask）。旧表 alert_channel_sub 保留不读写（legacy 只读展示）。
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
_PHONE_RE = re.compile(r"^1[3-9]\d{9}$")   # 批30：单源（auth_routes 手机号链 import 此处）


def _mask(channel: str, target: str | None) -> str | None:
    if not target:
        return target
    if channel == "sms" and len(target) == 11:
        return f"{target[:3]}****{target[-4:]}"
    return target


def _users_with_channels() -> list[dict]:
    """可订阅用户清单（enabled 未软删）+ 各自通道可用性（批34：勾选面=资料面——
    sms 可用性不再 and sms_configured()，凭证未配由发送侧跳过兜底，后配即生效）。"""
    with get_conn() as conn:
        cur = conn.execute(
            "SELECT u.id, u.username, u.nickname, u.email, u.phone FROM users u "
            "WHERE u.enabled AND u.deleted_at IS NULL ORDER BY u.id")
        users = [{"id": r[0], "username": r[1], "nickname": r[2], "email": r[3], "phone": r[4]}
                 for r in cur.fetchall()]
        cur = conn.execute(
            "SELECT id, owner_user_id, name FROM im_bot_config WHERE enabled "
            "AND owner_user_id IS NOT NULL ORDER BY id")
        by_user: dict[int, list] = {}
        for bid, owner, name in cur.fetchall():
            by_user.setdefault(owner, []).append({"id": bid, "name": name or f"bot-{bid}"})
    for u in users:
        u["channels"] = {"email": bool(u["email"]), "sms": bool(u["phone"]),
                         "bots": by_user.get(u["id"], [])}
        del u["email"], u["phone"]   # 明细不外泄（历史形状也无此二键）
    return users


def _subs_from_db() -> list[dict]:
    """软删/停用用户行不入列（A-P2-2 裁定=用户裁定"删用户自然从通知列表删除"——管理面与
    投递面同口径；孤儿行 inert，随旧表清理批扫除）。批34：带 channels 原始值。"""
    with get_conn() as conn:
        cur = conn.execute(
            "SELECT s.id, s.user_id, s.categories, s.min_level, s.enabled, s.channels, u.username, u.nickname "
            "FROM alert_user_sub s JOIN users u ON u.id=s.user_id "
            "AND u.enabled AND u.deleted_at IS NULL ORDER BY s.id")
        return [{"id": r[0], "user_id": r[1], "categories": r[2] or [], "min_level": r[3],
                 "enabled": r[4], "channels_raw": r[5], "username": r[6], "nickname": r[7]}
                for r in cur.fetchall()]


def _strip_dead_keys(sel: list | None, avail: dict) -> list | None:
    """批34：勾选值剥离失效键（用户裁定"通道删除则本处也删除"）——email/sms 资料已空、
    im:bid 实体已删的键不进前端回显；None 原样（全通道语义不代入）。畸形键一并滤除。"""
    if sel is None:
        return None
    bot_ids = {b["id"] for b in avail.get("bots", [])}
    out = []
    for k in sel:
        if k == "email" and avail.get("email"):
            out.append(k)
        elif k == "sms" and avail.get("sms"):
            out.append(k)
        elif str(k).startswith("im:") and str(k)[3:].isdigit() and int(k[3:]) in bot_ids:
            out.append(k)
    return out


def _legacy_from_db() -> list[dict]:
    """旧通道订阅清单（只读提示——**仅 email/sms 行**（A-P2-6：im 行已自动迁移，列入=误导
    admin 多余重建）；批30 §三过渡语义）。"""
    try:
        with get_conn() as conn:
            cur = conn.execute(
                "SELECT channel, target FROM alert_channel_sub WHERE channel IN ('email','sms') ORDER BY id")
            return [{"channel": r[0], "target": _mask(r[0], r[1])} for r in cur.fetchall()]
    except Exception:
        return []


@router.get("/api/alerts/config")
def alerts_config_get(payload: dict = Depends(require_perm("alerts_config"))):
    """订阅列表（批30 用户维度/批34 通道选择）+ 可订阅用户清单 + 旧表遗留行。
    批34 行形状：channels_sel=剥离失效键后的勾选（None=全通道/[]=零通道静音）、
    channels_avail=该用户可用通道（email/sms bool + bots 明细）。"""
    from src.alert_notify.sms import sms_configured
    from src.alert_notify.dispatch import _LIMITS
    users = _users_with_channels()
    avail_by_uid = {u["id"]: u["channels"] for u in users}
    subs = []
    for s in _subs_from_db():
        avail = avail_by_uid.get(s["user_id"], {"email": False, "sms": False, "bots": []})
        subs.append({"id": s["id"], "user_id": s["user_id"], "categories": s["categories"],
                     "min_level": s["min_level"], "enabled": s["enabled"],
                     "username": s["username"], "nickname": s["nickname"],
                     "channels_sel": _strip_dead_keys(s["channels_raw"], avail),
                     "channels_avail": avail})
    return {"subs": subs, "users": users, "sms_configured": sms_configured(),
            "legacy": _legacy_from_db(), "quota": dict(_LIMITS)}


def _validate_sub_body(body: dict, *, partial: dict | None = None) -> tuple[list, str, bool]:
    base = partial or {}
    cats = body.get("categories", base.get("categories", []))
    if not isinstance(cats, list) or any(c not in _CATEGORIES for c in cats):
        raise ApiError(400, "BAD_REQUEST", "categories 须为 risk/task/data/system 子集")
    min_level = body.get("min_level", base.get("min_level", "warn"))
    if min_level not in ("warn", "critical"):
        raise ApiError(400, "BAD_REQUEST", "min_level 须为 warn|critical")
    enabled = bool(body.get("enabled", base.get("enabled", True)))
    return cats, min_level, enabled


def _validate_channels(uid: int, channels) -> tuple[list | None, list]:
    """批34：通道勾选校验+失效剥离（盲审 A-P0-1 解法×用户裁定 2）。仅在 body 显式含
    channels 键时调用（缺键=沿用行现值——开关注 toggle 不重置勾选，B-P1-2）。
    三态：None=全通道/[]=零通道静音/非空=按勾选。返回 (落库值, 被剥离键)：
    - 实体已消失（bot 删/邮箱手机清空）→ 剥离落库（懒清理，审计注 stripped）
    - 实体在但不属该用户（bot 属他人/无邮箱勾 email/无手机勾 sms）→ 400 ALERT_CHANNEL_INVALID
    - 畸形键 → 400"""
    if channels is None:
        return None, []
    if not isinstance(channels, list) or any(not isinstance(k, str) for k in channels):
        raise ApiError(400, "ALERT_CHANNEL_INVALID", "channels 须为 null 或字符串数组")
    with get_conn() as conn:
        cur = conn.execute("SELECT email, phone FROM users WHERE id=%s", (uid,))
        u = cur.fetchone()
        cur = conn.execute("SELECT id FROM im_bot_config WHERE owner_user_id=%s AND enabled", (uid,))
        own_bots = {b[0] for b in cur.fetchall()}
    email_ok, phone_ok = (bool(u[0]), bool(u[1])) if u else (False, False)
    out: list[str] = []
    stripped: list[str] = []
    for k in channels:
        if k == "email":
            if not email_ok:
                raise ApiError(400, "ALERT_CHANNEL_INVALID",
                               "该用户未填邮箱，无法勾选邮件通道；请先让该用户在「个人资料」补上邮箱。")
            out.append(k)
        elif k == "sms":
            if not phone_ok:
                raise ApiError(400, "ALERT_CHANNEL_INVALID",
                               "该用户未填手机号，无法勾选短信通道；请先让该用户在「个人资料」补上手机号。")
            out.append(k)
        elif k.startswith("im:") and k[3:].isdigit():
            bid = int(k[3:])
            if bid in own_bots:
                out.append(f"im:{bid}")   # 规范化（盲审 A-P2-3：im:007 死键防线）
            else:
                with get_conn() as conn:
                    # 盲审 A-P2-2：owner 判定区分"属他人"（400）与"自有但停用/已删"（剥离）
                    cur = conn.execute(
                        "SELECT count(*) FROM im_bot_config WHERE id=%s AND owner_user_id=%s "
                        "AND enabled", (bid, uid))
                    own_disabled = cur.fetchone()[0] > 0
                if own_disabled:
                    stripped.append(k)   # 自有但停用=等同不可用，剥离（dispatch enabled 过滤天然不发）
                    continue
                with get_conn() as conn:
                    cur = conn.execute("SELECT count(*) FROM im_bot_config WHERE id=%s", (bid,))
                    exists = cur.fetchone()[0] > 0
                if exists:   # 实体在但不属该用户——API 误用信号
                    raise ApiError(400, "ALERT_CHANNEL_INVALID",
                                   f"所选机器人（ID {bid}）不属于该用户，请重新勾选。")
                stripped.append(k)   # 已删 bot=实体消失，剥离落库
        else:
            raise ApiError(400, "ALERT_CHANNEL_INVALID", f"通道键不合法: {k!r}")
    return out, stripped


@router.post("/api/alerts/config")
def alerts_config_create(body: dict = Body(...), payload: dict = Depends(require_perm("alerts_config"))):
    """新增订阅行（批30：维度=用户）。全表上限 10 行=最多 10 个订阅用户。"""
    try:
        uid = int(body.get("user_id") or 0)
    except (TypeError, ValueError):
        raise ApiError(400, "BAD_REQUEST", "user_id 须为数字")
    with get_conn() as conn:
        cur = conn.execute(
            "SELECT username FROM users WHERE id=%s AND enabled AND deleted_at IS NULL", (uid,))
        u = cur.fetchone()
        if not u:
            raise ApiError(400, "BAD_REQUEST", "用户不存在或不可订阅（停用/软删）")
        cur = conn.execute("SELECT count(*) FROM alert_user_sub")
        if cur.fetchone()[0] >= 10:   # 补审C 行数上限保形（per-channel 概念消失=全表 10 用户）
            raise ApiError(400, "BAD_REQUEST", "订阅用户至多 10 个")
    cats, min_level, enabled = _validate_sub_body(body)
    sel: list | None = None
    stripped: list = []
    if "channels" in body:   # 批34：显式提交才校验（缺键=INSERT 全通道缺省）
        sel, stripped = _validate_channels(uid, body.get("channels"))
    import json as _json
    with get_conn() as conn:
        try:
            cur = conn.execute(
                "INSERT INTO alert_user_sub (user_id, categories, min_level, enabled, channels) "
                "VALUES (%s, %s::jsonb, %s, %s, %s::jsonb) RETURNING id",
                (uid, _json.dumps(cats), min_level, enabled,
                 _json.dumps(sel) if sel is not None else None))
            new_id = cur.fetchone()[0]
            conn.commit()
        except Exception as e:
            conn.rollback()
            if type(e).__name__ == "UniqueViolation":
                raise ApiError(409, "DUPLICATE_SUB", "该用户已订阅")
            raise
    audit_log(payload["username"], "alerts_config_create",
              detail=f"#{new_id} user={u[0]} cats={cats} level={min_level} enabled={enabled}"
                     f" channels={sel if sel is not None else 'ALL'}"
                     + (f" stripped={stripped}" if stripped else ""))
    return {"id": new_id}


def _load_row(row_id: int) -> dict:
    with get_conn() as conn:
        cur = conn.execute(
            "SELECT s.id, s.user_id, s.categories, s.min_level, s.enabled, s.channels, u.username "
            "FROM alert_user_sub s JOIN users u ON u.id=s.user_id "
            "AND u.enabled AND u.deleted_at IS NULL WHERE s.id=%s", (row_id,))   # A-P2-7：软删用户行不可操作（含 test 面不再对死号发真实短信）
        r = cur.fetchone()
    if not r:
        raise ApiError(404, "ROW_NOT_FOUND", f"订阅行 {row_id} 不存在")
    return {"id": r[0], "user_id": r[1], "categories": r[2] or [], "min_level": r[3],
            "enabled": r[4], "channels": r[5], "username": r[6]}


@router.put("/api/alerts/config/{row_id}")
def alerts_config_update(row_id: int, body: dict = Body(...),
                         payload: dict = Depends(require_perm("alerts_config"))):
    """改订阅行（批30：用户不可改——换人=删了重建；批34：channels 缺键=沿用现值
    ——行内开关注 toggle 不带 channels 不重置勾选，B-P1-2；显式提交才校验+剥离失效键）。"""
    row = _load_row(row_id)
    if "user_id" in body:
        try:
            new_uid = int(body.get("user_id") or 0)
        except (TypeError, ValueError):
            raise ApiError(400, "BAD_REQUEST", "user_id 须为数字")
        if new_uid != row["user_id"]:
            raise ApiError(400, "BAD_REQUEST", "订阅用户不可改（删除后重新添加）")
    cats, min_level, enabled = _validate_sub_body(body, partial=row)
    sel: list | None = row["channels"]
    stripped: list = []
    if "channels" in body:
        sel, stripped = _validate_channels(row["user_id"], body.get("channels"))
    import json as _json
    with get_conn() as conn:
        conn.execute(
            "UPDATE alert_user_sub SET categories=%s::jsonb, min_level=%s, enabled=%s, "
            "channels=%s::jsonb, updated_at=now() WHERE id=%s",
            (_json.dumps(cats), min_level, enabled,
             _json.dumps(sel) if sel is not None else None, row_id))
        conn.commit()
    audit_log(payload["username"], "alerts_config_update",
              detail=f"#{row_id} user={row['username']} cats={cats} level={min_level} enabled={enabled}"
                     f" channels={sel if sel is not None else 'ALL'}"
                     + (f" stripped={stripped}" if stripped else ""))


@router.delete("/api/alerts/config/{row_id}")
def alerts_config_delete(row_id: int, payload: dict = Depends(require_perm("alerts_config"))):
    row = _load_row(row_id)
    with get_conn() as conn:
        conn.execute("DELETE FROM alert_user_sub WHERE id=%s", (row_id,))
        conn.commit()
    audit_log(payload["username"], "alerts_config_delete", detail=f"#{row_id} user={row['username']}")
    return {"ok": True}


@router.get("/api/alerts/sms-config")
def sms_config_get(payload: dict = Depends(require_perm("alerts_config"))):
    """凭证状态：只回 secret_set 布尔与非密钥项（access_key_id 不回显）。"""
    from src.alert_notify.sms import _sms_config, sms_configured
    cfg = _sms_config() or {}
    # verify 模板键独立读（不入 _CFG_KEYS——A-P1-5：未配验证码模板不应影响告警面判定）
    verify_tpl = ""
    try:
        with get_conn() as conn:
            r = conn.execute(
                "SELECT value FROM system_config WHERE key='alert_sms_verify_template_code'").fetchone()
        verify_tpl = str(r[0]).strip() if r and r[0] else ""
    except Exception:
        pass
    return {"secret_set": bool(cfg.get("alert_sms_access_key_secret")),
            "sms_configured": sms_configured(),
            "sign_name": cfg.get("alert_sms_sign_name", ""),
            "template_code": cfg.get("alert_sms_template_code", ""),
            "verify_template_code": verify_tpl}


@router.put("/api/alerts/sms-config")
def sms_config_put(body: dict = Body(...), payload: dict = Depends(require_perm("alerts_config"))):
    from src.quant_common.crypto import encrypt
    fields = {"access_key_id": "alert_sms_access_key_id",
              "access_key_secret": "alert_sms_access_key_secret",
              "sign_name": "alert_sms_sign_name",
              "template_code": "alert_sms_template_code",
              "verify_template_code": "alert_sms_verify_template_code"}   # 批30：验证码模板（明文 text）
    with get_conn() as conn:
        for k, col in fields.items():
            v = (body.get(k) or "").strip()
            if not v:
                continue   # 留空=不修改（smtp 先例）
            if k == "access_key_secret":
                v = encrypt(v)
            conn.execute(
                "INSERT INTO system_config (key, value, value_type, description) "
                f"VALUES ('{col}', %s, {'%r' % ('password' if k == 'access_key_secret' else 'text')}, '阿里云短信凭证（批7/批30）') "
                "ON CONFLICT (key) DO UPDATE SET value=EXCLUDED.value",
                (v,))
        conn.commit()
    audit_log(payload["username"], "alerts_sms_config_save",
              detail=f"keys={[k for k in fields if (body.get(k) or '').strip()]}")
    return {"ok": True}


@router.post("/api/alerts/test")
def alerts_test(body: dict = Body(...), payload: dict = Depends(require_perm("alerts_config"))):
    """用户维度测试发送（批30：按订阅行用户的三通道各试一遍绕节流/配额；批34：按勾选
    过滤——None=全试/[]=零通道/per-actor 60s 冷却；结果落 code=alert.test 站内通知。
    ok=已试通道全过（零可试通道=False）。"""
    try:
        _rid = int(body.get("id") or 0)
    except (TypeError, ValueError):
        raise ApiError(400, "BAD_REQUEST", "id 须为数字")
    row = _load_row(_rid)
    from src.alert_notify.dispatch import _ch_ok   # 与投递面同源谓词（盲审 A-P2-3）
    sel = row["channels"]
    actor = payload["username"]
    r = get_redis()
    cool = f"alert:test:cooldown:{actor}:{row['id']}"
    if not r.set(cool, "1", nx=True, ex=60):
        raise ApiError(429, "TOO_MANY_REQUESTS", "测试冷却中（60s/订阅行）")
    with get_conn() as conn:
        cur = conn.execute("SELECT email, phone FROM users WHERE id=%s", (row["user_id"],))
        u = cur.fetchone() or ("", "")
        cur = conn.execute("SELECT id FROM im_bot_config WHERE owner_user_id=%s AND enabled ORDER BY id",
                           (row["user_id"],))
        bots = [b[0] for b in cur.fetchall()]
    results: list[tuple[bool, str]] = []

    # —— 邮箱（有值即试——outbox 60s 兜，发送记录页可查）——
    if u[0] and _ch_ok(sel, "email"):
        try:
            from src.email_service import queue_email
            queue_email(u[0], "[test] 告警通道测试", "<pre>这是一封测试邮件（设置→告警→测试）</pre>")
            results.append((True, "邮件已入发送队列"))
        except Exception:
            results.append((False, "邮件入队失败: smtp_error"))

    # —— 短信（有手机且凭证齐）——
    if u[1] and _ch_ok(sel, "sms"):
        from src.alert_notify.sms import send_sms, sms_configured
        if not sms_configured():
            results.append((False, "短信未接入（凭证未配）"))
        else:
            ok, reason = send_sms(u[1], "info", "告警通道测试")
            results.append((ok, "短信已发送" if ok else f"短信失败: {reason}"))

    # —— IM（名下每个 enabled bot——沿用批 8.5 真发语义：test_connection + 发绑定用户）——
    for bid in bots:
        if not _ch_ok(sel, f"im:{bid}"):
            continue
        from src.im_bot.base import get_im_provider
        with get_conn() as conn:
            cur = conn.execute("SELECT provider FROM im_bot_config WHERE id=%s AND enabled", (bid,))
            b = cur.fetchone()
            cur = conn.execute("SELECT count(*), array_agg(im_user_id) FROM im_bot_users WHERE bot_id=%s",
                               (bid,))
            agg = cur.fetchone()
        n_bound, bound = (agg[0] or 0), (agg[1] or [])
        if not b:
            results.append((False, f"机器人 {bid} 不存在或未启用"))
            continue
        p = get_im_provider(b[0])
        if not p:
            results.append((False, f"机器人 {bid} provider 缺失"))
            continue
        ok, detail = p.test_connection(bid)
        if not ok:
            results.append((False, f"机器人 {bid} 连接失败: {str(detail)[:40]}"))
            continue
        if n_bound == 0:
            results.append((False, f"机器人 {bid} 无绑定用户（先对它发条消息完成绑定）"))
            continue
        sent = sum(1 for uu in bound
                   if p.send_text(bid, uu, "open_id", "[test] 告警通道测试——收到本条即 IM 告警链路已通"))
        results.append((sent > 0, f"机器人 {bid} 连接正常，测试消息已发 {sent}/{n_bound} 位绑定用户"))

    if not results:
        if sel == []:
            ok, detail = False, "未勾选任何通道，测试消息未发出；打开「编辑」勾选通道后再试。"
        elif sel:   # 勾了但全失效（bot 删/凭证未配/资料空——盲审 A-P2-4，文案师终稿）
            ok, detail = False, ("所选通道都已失效，测试消息未发出（如机器人已删除、短信未接入、"
                                 "未填邮箱/手机号）。请打开「编辑」检查勾选或改选可用通道。")
        else:
            ok, detail = False, "该用户没有可用通道（未设邮箱/手机，且名下无启用机器人）"
    else:
        ok = all(r0 for r0, _ in results)
        detail = "；".join(d for _, d in results)
    try:
        from src.alert_notify.notify import notify
        notify("info", "system", f"告警测试[user] · {'成功' if ok else '失败'}",
              f"{detail}（by {actor}）", code="alert.test")
    except Exception as e:
        import logging
        logging.getLogger("web_api").warning("alert.test notify failed: %s", e)
    audit_log(payload["username"], "alerts_test", detail=f"user={row['username']} ok={ok} {detail[:120]}")
    return {"ok": ok, "detail": detail}
