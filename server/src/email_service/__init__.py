"""SMTP 邮件发送服务（复用 safebox 模式）。

.env 配置：
  SMTP_HOST / SMTP_PORT / SMTP_USERNAME / SMTP_PASSWORD / SMTP_FROM
  未配置 SMTP_USERNAME 时走 DEV 模式（打印不发）。
"""
import smtplib
import os
import asyncio
import logging
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

_logger = logging.getLogger("quant")


def _smtp_config() -> tuple[str, int, str, str, str, str] | None:
    """SMTP 配置：仅读 system_config（Web「系统配置」页维护，单一真相源，2026-08-14 弃 .env）。
    返回 (host, port, security, username, password, from) 或 None（未配置）。
    security: auto（按端口推断 RFC 8314：465→ssl，其余→starttls）/ ssl / starttls。"""
    cfg = {}
    try:
        from src.data_platform.db import get_conn
        from src.quant_common.crypto import decrypt
        with get_conn() as conn:
            cur = conn.execute(
                "SELECT key, value FROM system_config WHERE key LIKE 'smtp_%'")
            for k, v in cur.fetchall():
                if v is None or str(v).strip() == "":
                    continue
                cfg[k] = decrypt(v) if k == "smtp_password" else str(v).strip()
    except Exception as e:
        _logger.error("read smtp config from DB failed: %s", e)
        return None
    if not cfg.get("smtp_username"):
        return None  # 未配置
    security = cfg.get("smtp_security", "auto")
    # auto：按端口推断（RFC 8314 业界约定）
    if security == "auto":
        security = "ssl" if cfg.get("smtp_port", "587") == "465" else "starttls"
    return (
        cfg.get("smtp_host", ""),
        int(cfg.get("smtp_port", "587") or 587),
        security,
        cfg["smtp_username"],
        cfg.get("smtp_password", ""),
        cfg.get("smtp_from") or cfg["smtp_username"],
    )


def _send_email_sync(to: str, subject: str, html_body: str) -> str | None:
    """底层同步发送邮件。成功返回 None，失败返回错误描述（供发件箱记录 last_error）。
    未配置：本地开发可 .env SMTP_DEV=true 显式开打印模式（不真发）；否则视为失败（→重试→铃铛）。"""
    conf = _smtp_config()
    if conf is None:
        if os.environ.get("SMTP_DEV") == "true":
            print(f"[DEV] SMTP 未配置（打印模式） -> {to}\n[DEV] 主题={subject}\n[DEV] 内容={html_body}")
            return None
        return "SMTP 未配置（Web 系统设置→系统配置 填 smtp_* 五项）"
    smtp_host, smtp_port, security, smtp_username, smtp_password, smtp_from = conf
    if not smtp_host:
        return "SMTP 未配置 smtp_host"

    msg = MIMEMultipart()
    msg["From"] = smtp_from
    msg["To"] = to
    msg["Subject"] = subject
    msg.attach(MIMEText(html_body, "html"))

    try:
        # security 已解析为 ssl（隐式，SMTP_SSL）/ starttls（明文连接后升级）
        cls = smtplib.SMTP_SSL if security == "ssl" else smtplib.SMTP
        with cls(smtp_host, smtp_port, timeout=60) as server:
            if security != "ssl":
                server.starttls()
            server.login(smtp_username, smtp_password)
            server.sendmail(smtp_from, to, msg.as_string())
        _logger.info("email sent: to=%s subject=%s", to, subject)
        return None
    except (smtplib.SMTPException, OSError) as e:
        _logger.error("email send failed: to=%s subject=%s err=%s", to, subject, e)
        return str(e) or type(e).__name__


# ——— 发件箱（持久化 + 指数退避重发；进程重启不丢，Celery beat 每分钟扫描）———

MAX_ATTEMPTS = 6  # 失败 6 次后标 failed（退避 1→2→4→8→16→30 分钟，约 1 小时）


def _backoff_seconds(failed_count: int) -> int:
    """第 failed_count 次失败后的下次等待：60*2^(n-1)，封顶 30 分钟。"""
    return min(60 * (2 ** (failed_count - 1)), 1800)


def _final_failure_notify(to: str, subject: str, err: str, outbox_id: int) -> None:
    """重试耗尽 → 通知中心（critical/email，admin 铃铛可见，点击直达发件箱）。失败不影响主流程。"""
    try:
        from src.alert_notify import notify
        notify("critical", "email", "邮件发送最终失败",
               f"收件人: {to}\n主题: {subject}\n重试 {MAX_ATTEMPTS} 次耗尽\n错误: {err}",
               source_ref=str(outbox_id), code="email.failed")
    except Exception as e:
        _logger.error("final failure notify error: %s", e)


def queue_email(to: str, subject: str, html_body: str) -> int:
    """入队（落 PG）。返回 outbox id；发送由 try_row 立即试发或 beat 扫描重发。"""
    from src.data_platform.db import get_conn
    with get_conn() as conn:
        cur = conn.execute(
            "INSERT INTO email_outbox (to_email, subject, html_body) VALUES (%s,%s,%s) RETURNING id",
            (to, subject, html_body))
        conn.commit()
        return cur.fetchone()[0]


def _try_row_sync(outbox_id: int) -> None:
    """认领（pending→sending）并单次发送；成功标 sent，失败按指数退避排下次，超上限标 failed。"""
    from src.data_platform.db import get_conn
    with get_conn() as conn:
        cur = conn.execute(
            "UPDATE email_outbox SET status='sending' "
            "WHERE id=%s AND status='pending' AND next_attempt_at<=now() "
            "RETURNING id, to_email, subject, html_body, attempts", (outbox_id,))
        row = cur.fetchone()
        if not row:
            conn.rollback()
            return
        conn.commit()
    _, to, subject, body, attempts = row
    err = _send_email_sync(to, subject, body)
    with get_conn() as conn:
        if err is None:
            conn.execute(
                "UPDATE email_outbox SET status='sent', sent_at=now(), last_error=NULL WHERE id=%s", (outbox_id,))
        else:
            n = attempts + 1
            if n >= MAX_ATTEMPTS:
                conn.execute(
                    "UPDATE email_outbox SET status='failed', attempts=%s, last_error=%s WHERE id=%s",
                    (n, err, outbox_id))
                _final_failure_notify(to, subject, err, outbox_id)
            else:
                conn.execute(
                    "UPDATE email_outbox SET status='pending', attempts=%s, next_attempt_at=now()+make_interval(secs=>%s), last_error=%s WHERE id=%s",
                    (n, _backoff_seconds(n), err, outbox_id))
        conn.commit()


async def try_row(outbox_id: int) -> None:
    """立即试发一次（接口后台任务调用；失败留给 beat 重发）。"""
    await asyncio.get_running_loop().run_in_executor(None, _try_row_sync, outbox_id)


def sweep(limit: int = 3) -> int:
    """扫描到期待发邮件并逐封发送（Celery beat 每分钟调；limit 限制单轮防超 Celery 5min 时限）。"""
    from src.data_platform.db import get_conn
    with get_conn() as conn:
        cur = conn.execute(
            "SELECT id FROM email_outbox WHERE status='pending' AND next_attempt_at<=now() ORDER BY id LIMIT %s",
            (limit,))
        ids = [r[0] for r in cur.fetchall()]
    for i in ids:
        _try_row_sync(i)
    if ids:
        _logger.info("email outbox swept: %d row(s)", len(ids))
    return len(ids)


def _resolve_base_url(request_base: str = "") -> str:
    """邮件链接 base 优先级：system_config.base_url（非空，Web 可改）> 请求 hostname > .env BASE_URL。

    缺省取 hostname：管理员从哪个域名访问就用哪个域名，自适应、不硬编码。
    """
    # 1. DB 配置（Web「系统配置」页可改；留空表示走 hostname）
    try:
        from src.data_platform.db import get_conn
        with get_conn() as conn:
            cur = conn.execute("SELECT value FROM system_config WHERE key='base_url'")
            row = cur.fetchone()
            if row and row[0] and str(row[0]).strip():
                return str(row[0]).strip().rstrip("/")
    except Exception:
        pass
    # 2. 缺省取访问 hostname
    if request_base:
        return request_base.rstrip("/")
    # 3. .env BASE_URL（开发期/兼容）
    env_url = os.environ.get("BASE_URL", "").strip().rstrip("/")
    if env_url:
        return env_url
    # 4. 兜底（理论上不会到这，请求总带 hostname）
    return "https://quant.snailtrail.cc"


# ——— 邮件模板（N 语言 dict，en 为缺省；新增语言 = 加一个条目，逻辑零改动）———
# 语言来源：操作界面当前语言（前端随请求传 lang），未匹配回落 en。

_BTN = 'style="display: inline-block; padding: 12px 24px; background: {color}; color: white; text-decoration: none; border-radius: 6px;"'

INVITE_TPL: dict[str, dict[str, str]] = {
    "zh": {
        "subject": "人工智能开发学习平台 · 邀请开通",
        "body": """<html><body style="font-family: sans-serif; max-width: 480px; margin: 0 auto;">
        <h2>📧 邀请开通</h2>
        <p>您被邀请开通平台账号。</p>
        <p style="margin: 20px 0;">
            <a href="{register_url}" {btn}>点击开通账号</a>
        </p>
        <p style="color: #666; font-size: 14px;">链接 3 天内有效。</p>
    </body></html>""",
    },
    "en": {
        "subject": "AI Development Learning · You're Invited",
        "body": """<html><body style="font-family: sans-serif; max-width: 480px; margin: 0 auto;">
        <h2>📧 You're Invited</h2>
        <p>You are invited to open an account on the platform.</p>
        <p style="margin: 20px 0;">
            <a href="{register_url}" {btn}>Open Account</a>
        </p>
        <p style="color: #666; font-size: 14px;">Link valid for 3 days.</p>
    </body></html>""",
    },
}

RESET_TPL: dict[str, dict[str, str]] = {
    "zh": {
        "subject": "人工智能开发学习平台 · 密码重置",
        "body": """<html><body style="font-family: sans-serif; max-width: 480px; margin: 0 auto;">
        <h2>🔐 密码重置</h2>
        <p>您请求重置密码。</p>
        <p style="margin: 20px 0;">
            <a href="{reset_url}" {btn_red}>点击重置密码</a>
        </p>
        <p style="color: #666; font-size: 14px;">链接 1 小时内有效。</p>
        <p style="color: #999; font-size: 12px;">如果不是您本人操作，请忽略此邮件。</p>
    </body></html>""",
    },
    "en": {
        "subject": "AI Development Learning · Password Reset",
        "body": """<html><body style="font-family: sans-serif; max-width: 480px; margin: 0 auto;">
        <h2>🔐 Password Reset</h2>
        <p>You requested a password reset.</p>
        <p style="margin: 20px 0;">
            <a href="{reset_url}" {btn_red}>Reset Password</a>
        </p>
        <p style="color: #666; font-size: 14px;">Link valid for 1 hour.</p>
        <p style="color: #999; font-size: 12px;">If this wasn't you, please ignore this email.</p>
    </body></html>""",
    },
}

ACTIVATION_TPL: dict[str, dict[str, str]] = {
    "zh": {
        "subject": "账号已开通 · 人工智能开发学习平台",
        "body": """<html><body style="font-family: sans-serif; max-width: 560px; margin: 0 auto;">
        <h2>✅ 账号开通成功</h2>
        <p>您的账号已开通，可登录使用。</p>
        <p>用户名：<b>{username}</b><br/>
           权限：Viewer。</p>
        <p style="margin: 20px 0;">
            <a href="{login_url}" {btn}>点击登录</a>
        </p>
        <hr/>{terms_stacked}
    </body></html>""",
    },
    "en": {
        "subject": "Account Activated · AI Development Learning",
        "body": """<html><body style="font-family: sans-serif; max-width: 560px; margin: 0 auto;">
        <h2>✅ Account Activated</h2>
        <p>Your account is ready. You can sign in now.</p>
        <p>Username: <b>{username}</b><br/>
           Role: Viewer.</p>
        <p style="margin: 20px 0;">
            <a href="{login_url}" {btn}>Sign In</a>
        </p>
        <hr/>{terms_stacked}
    </body></html>""",
    },
}


def normalize_lang(lang: str | None) -> str:
    """语言归一化：请求语言在已实现语言内则用之，否则回落 en（国际通用缺省）。"""
    lang = (lang or "").strip().lower()
    from src.quant_common.terms import available_langs
    return lang if lang in available_langs() else "en"


def _render(tpl_table: dict, lang: str, **fields) -> tuple[str, str]:
    """按语言选模板（en 缺省）并填充占位符，返回 (subject, body)。"""
    tpl = tpl_table.get(lang) or tpl_table["en"]
    btn = _BTN.format(color="#409eff")
    btn_red = _BTN.format(color="#f56c6c")
    body = tpl["body"].format(btn=btn, btn_red=btn_red, **fields)
    return tpl["subject"], body


def _terms_stacked_html() -> str:
    """条款全语言纵向堆叠（注册表驱动，新增语言自动包含；引言双语固定说明）。"""
    from src.quant_common.terms import get_terms_items
    parts = [
        '<p style="color: #666; font-size: 14px;">以下是《平台使用条款》，请务必认真阅读：<br/>'
        'Terms of Use in all available languages below. Please read carefully:</p>'
    ]
    for i, item in enumerate(get_terms_items()):
        h_style = 'style="color: #303133;' + (' margin-top: 24px;' if i else '') + '"'
        text = (item["body"].replace("&", "&amp;").replace("<", "&lt;")
                .replace(">", "&gt;").replace("\n", "<br>"))
        parts.append(f'<h3 {h_style}>{item["name"]}</h3>')
        parts.append(f'<div style="font-size: 14px; color: #606266; line-height: 1.7; white-space: pre-wrap;">{text}</div>')
    return "\n".join(parts)


async def send_invite_email(email: str, token: str, request_base: str = "", lang: str = "en") -> bool:
    """发送邀请开通邮件（语言=邀请者操作界面语言，en 缺省）。"""
    base_url = _resolve_base_url(request_base)
    register_url = f"{base_url}/register?token={token}"
    lang = normalize_lang(lang)
    subject, body = _render(INVITE_TPL, lang, register_url=register_url)
    _logger.info("send invite email: to=%s lang=%s base_url=%s register_url=%s", email, lang, base_url, register_url)
    outbox_id = queue_email(email, subject, body)
    await try_row(outbox_id)
    return True


async def send_password_reset_email(email: str, token: str, request_base: str = "", lang: str = "en") -> bool:
    """发送密码重置邮件（语言=请求者操作界面语言，en 缺省）。"""
    base_url = _resolve_base_url(request_base)
    reset_url = f"{base_url}/reset-password?token={token}"
    lang = normalize_lang(lang)
    subject, body = _render(RESET_TPL, lang, reset_url=reset_url)
    outbox_id = queue_email(email, subject, body)
    await try_row(outbox_id)
    return True


async def send_activation_email(email: str, username: str, request_base: str = "", lang: str = "en") -> bool:
    """开通成功通知邮件：登录链接 + 条款全语言纵向堆叠（语言=注册者操作界面语言）。"""
    base_url = _resolve_base_url(request_base)
    login_url = f"{base_url}/login"
    lang = normalize_lang(lang)
    subject, body = _render(ACTIVATION_TPL, lang, username=username, login_url=login_url,
                            terms_stacked=_terms_stacked_html())
    _logger.info("send activation email: to=%s username=%s lang=%s base_url=%s", email, username, lang, base_url)
    outbox_id = queue_email(email, subject, body)
    await try_row(outbox_id)
    return True
