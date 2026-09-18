"""SMTP 邮件发送服务（复用 safebox 模式）。

批47：多通道 failover——`smtp_provider` 表（集成中心→邮件 页维护，行序即 failover 顺序）；
每通道尝试配额 system_config `smtp_max_attempts`（默认 3）；无通道时 .env SMTP_DEV=true
走 DEV 模式（打印不发）。system_config 旧 smtp_* 六键已迁移退役（2026-08-14 曾弃 .env 单实例）。
"""
import smtplib
import os
import asyncio
import logging
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

_logger = logging.getLogger("quant")


def _providers() -> list[dict]:
    """批47：按 position 序读 enabled 通道（行序即 failover 顺序——用户裁定；密码解密）。
    返回 [{id,host,port,security,username,password,from}]；空=未配置。security auto 按端口
    推断（RFC 8314：465→ssl，其余→starttls）——与原 _smtp_config 同语义。"""
    out = []
    try:
        from src.data_platform.db import get_conn
        from src.quant_common.crypto import decrypt
        with get_conn() as conn:
            cur = conn.execute(
                "SELECT id, host, port, security, username, password, from_addr "
                "FROM smtp_provider WHERE enabled ORDER BY position, id")
            for pid, host, port, sec, user, pwd, frm in cur.fetchall():
                if not str(user or "").strip():
                    continue   # username 空=未配置（原 _smtp_config 判定同口径）
                sec = (sec or "auto").strip() or "auto"
                port = int(port or 587)
                if sec == "auto":
                    sec = "ssl" if port == 465 else "starttls"
                out.append({"id": pid, "host": host or "", "port": port, "security": sec,
                            "username": user, "password": decrypt(pwd) if pwd else "",
                            "from": (frm or "").strip() or user})
    except Exception as e:
        _logger.error("read smtp providers failed: %s", e)
    return out


def _max_attempts() -> int:
    """批47：每通道尝试配额（system_config smtp_max_attempts，默认 3；**总尝试=配额**——用户
    裁定 A：3=该通道共试 3 次后切下一条）。读失败回落 3。"""
    try:
        from src.data_platform.db import get_conn
        with get_conn() as conn:
            row = conn.execute("SELECT value FROM system_config WHERE key='smtp_max_attempts'").fetchone()
        return max(1, int(str(row[0]).strip())) if row and str(row[0]).strip() else 3
    except Exception:
        return 3


def _send_email_sync(to: str, subject: str, html_body: str, provider: dict | None) -> str | None:
    """底层同步发送邮件（批47 改收 provider 实例 dict——多通道 failover 由 _try_row_sync 状态机
    驱动）。成功返回 None，失败返回错误描述（供发件箱记录 last_error）。
    未配置（providers 空）：本地开发可 .env SMTP_DEV=true 显式开打印模式（不真发）；否则视为
    失败（→重试→铃铛）。"""
    if provider is None:
        if os.environ.get("SMTP_DEV") == "true":
            print(f"[DEV] SMTP 未配置（打印模式） -> {to}\n[DEV] 主题={subject}\n[DEV] 内容={html_body}")
            return None
        return "SMTP 未配置（集成中心→邮件 添加通道）"
    smtp_host = provider["host"]
    if not smtp_host:
        return "SMTP 通道未配置服务器地址"
    smtp_port, security = provider["port"], provider["security"]
    smtp_username, smtp_password, smtp_from = provider["username"], provider["password"], provider["from"]

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
        _logger.info("email sent: to=%s subject=%s provider=%s", to, subject, smtp_host)
        return None
    except (smtplib.SMTPException, OSError) as e:
        _logger.error("email send failed: to=%s subject=%s provider=%s err=%s", to, subject, smtp_host, e)
        return str(e) or type(e).__name__


# ——— 发件箱（持久化 + 指数退避重发；进程重启不丢，Celery beat 每分钟扫描）———

# 批47：MAX_ATTEMPTS=6 退役——多通道 failover 后总上限=通道数×smtp_max_attempts（动态）。
# 死行回收独立绝对上限（固定 30）：防"已发成功写库前死"行被无限重发真实邮件（批27-1 核心保障）；
# 与业务配额独立——配额小则业务 failed 先达，配额大则死行提前 failed=安全方向（盲审 A-P1-1）。
SWEEP_ABS_LIMIT = 30


def _backoff_seconds(failed_count: int) -> int:
    """第 failed_count 次失败后的下次等待：60*2^(n-1)，封顶 30 分钟。"""
    return min(60 * (2 ** (failed_count - 1)), 1800)


def _final_failure_notify(to: str, subject: str, err: str, outbox_id: int) -> None:
    """重试耗尽（全部通道轮转一圈）→ 通知中心（critical/email，admin 铃铛可见，点击直达发件箱）。
    失败不影响主流程。批47 防递归：dispatch 侧 code=email.failed 跳过 email 通道外推——
    否则本通知自己再走 N 通道×配额=慢速自持续链。"""
    try:
        from src.alert_notify import notify
        notify("critical", "email", "邮件发送最终失败",
               f"收件人: {to}\n主题: {subject}\n全部 SMTP 通道重试耗尽\n错误: {err}",
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
        from src.data_platform.log_sink import event   # 批25：发送事件进 system_log（入队=WARN 待发）
        event("WARN", "email", f"入队待发 → {to} ｜ {subject}")
        return cur.fetchone()[0]


def _try_row_sync(outbox_id: int) -> None:
    """认领（pending→sending）并单次发送；批47 多通道 failover 状态机：
    - 实例解析：行 provider_id null/悬空（实例删/停用→不在 enabled 列表）→回落第一实例
      （与"新邮件从第一个开始"同精神——盲审 A-P1-2）
    - 失败且 provider_attempts+1 < 配额 → **同实例**指数退避重试（既有节奏）
    - 配额尽 → 切下一实例（providers 按 position 序，idx+1 即严格下一个——禁 position+1 裸算）：
      provider_id=下一/provider_attempts=0/pending，next=now()+60s（切换短退避防连环打爆）
    - 已最后实例（轮转一圈）或无实例 → failed 终态+最终失败通知
    成功标 sent（批27-1④回写带 AND status='sending' 防与回收-重领重叠双写——保留）。

    批27-1：claim 段借 next_attempt_at 写认领超时锚（now()+10min）——进程死在 SMTP 60s 窗口/
    写库前时行停在 sending，sweep 的死行回收（见 sweep）按锚过期重置，邮件不再静默永丢。"""
    from src.data_platform.db import get_conn
    with get_conn() as conn:
        cur = conn.execute(
            "UPDATE email_outbox SET status='sending', next_attempt_at=now()+interval '10 min' "
            "WHERE id=%s AND status='pending' AND next_attempt_at<=now() "
            "RETURNING id, to_email, subject, html_body, attempts, provider_id, provider_attempts",
            (outbox_id,))
        row = cur.fetchone()
        if not row:
            conn.rollback()
            return
        conn.commit()
    _, to, subject, body, attempts, prov_id, prov_att = row
    providers = _providers()
    idx = next((i for i, p in enumerate(providers) if p["id"] == prov_id), 0)
    quota = _max_attempts()
    provider = providers[idx] if providers else None
    err = _send_email_sync(to, subject, body, provider)
    # 批25：终态事件进 system_log（四路径必经单点）；盲审 A-P2-6——事件在 commit 成功后发（防 UPDATE 回滚与日志不一致）
    from src.data_platform.log_sink import event
    _ev = None
    with get_conn() as conn:
        if err is None:
            # 批27-1④：回写加 AND status='sending'——与回收-重领重叠时不双写（盲审 A）
            conn.execute(
                "UPDATE email_outbox SET status='sent', sent_at=now(), last_error=NULL "
                "WHERE id=%s AND status='sending'", (outbox_id,))
            _ev = ("INFO", f"已发送 → {to} ｜ {subject}")
        else:
            n = attempts + 1
            if provider is not None and prov_att + 1 < quota:
                conn.execute(
                    "UPDATE email_outbox SET status='pending', attempts=%s, provider_attempts=%s, "
                    "next_attempt_at=now()+make_interval(secs=>%s), last_error=%s "
                    "WHERE id=%s AND status='sending'",
                    (n, prov_att + 1, _backoff_seconds(prov_att + 1), err, outbox_id))   # 批47 盲审 A-P1-1：通道内计数做指数基数（attempts 全局累计会让后位通道退避放大成小时级——每通道独立节奏才是容灾语义）
                _ev = ("WARN", f"待重发（第 {prov_att + 1}/{quota} 次·通道 {provider['host']}）→ {to} ｜ {subject} ｜ {err}")
            elif provider is not None and idx + 1 < len(providers):
                nxt = providers[idx + 1]
                conn.execute(
                    "UPDATE email_outbox SET status='pending', attempts=%s, provider_id=%s, provider_attempts=0, "
                    "next_attempt_at=now()+interval '60 seconds', last_error=%s "
                    "WHERE id=%s AND status='sending'",
                    (n, nxt["id"], err, outbox_id))
                _ev = ("WARN", f"通道 {provider['host']} 重试耗尽，60 秒后切 {nxt['host']} → {to} ｜ {subject} ｜ {err}")
            else:
                conn.execute(
                    "UPDATE email_outbox SET status='failed', attempts=%s, last_error=%s "
                    "WHERE id=%s AND status='sending'",
                    (n, err, outbox_id))
                _ev = ("ERROR", f"发送失败（全部通道重试耗尽）→ {to} ｜ {subject} ｜ {err}")
        conn.commit()
    if _ev:
        event(_ev[0], "email", _ev[1])
        if _ev[0] == "ERROR":
            _final_failure_notify(to, subject, err, outbox_id)


async def try_row(outbox_id: int) -> None:
    """立即试发一次（接口后台任务调用；失败留给 beat 重发）。"""
    await asyncio.get_running_loop().run_in_executor(None, _try_row_sync, outbox_id)


def sweep(limit: int = 3) -> int:
    """扫描到期待发邮件并逐封发送（Celery beat 每分钟调；limit 限制单轮防超 Celery 5min 时限）。

    批27-1②：开头先回收 sending 死行（claim 锚 10min 过期=进程死在发送窗/写库前）——
    attempts+1 计次（防"已发成功但写库前死"→每 10 分钟无限重发真实邮件，盲审 A），
    达上限直接标 failed 走终态可见（回收不触发 _final_failure_notify——轻量，失败事件
    由后续真实发送路径产生）；回收与 claim 条件词互斥（sending vs pending）无竞态。
    批47：上限改 SWEEP_ABS_LIMIT=30 独立绝对值（MAX_ATTEMPTS 退役后业务上限=通道数×配额动态，
    回收纯 SQL 不便读动态值；**不动 provider_attempts**——进程死≠实例故障，动了会因反复重启
    烧配额误切实例——盲审 A-P2-1 反转）。"""
    from src.data_platform.db import get_conn
    with get_conn() as conn:
        conn.execute(
            "UPDATE email_outbox "
            "SET status=CASE WHEN attempts+1>=%s THEN 'failed' ELSE 'pending' END, "
            "    attempts=attempts+1, last_error=COALESCE(last_error, '发送窗中断（进程重启/回收）') "
            "WHERE status='sending' AND next_attempt_at<=now()",
            (SWEEP_ABS_LIMIT,))
        conn.commit()   # 盲审 B：with 退出=还池回滚——回收必须显式 commit
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


# 批20：改邮箱确认邮件（验证成功才改——文案经文案师；正文带 1 小时时效对齐找回密码格式）。
# 盲审B-P0：`{url}` 单花括号（{{}} 是 .format 转义→字面 {url} 永不渲染）；HTML 按钮对齐 RESET_TPL（B-P2-5）
EMAIL_CHANGE_TPL = {
    "zh": {"subject": "人工智能开发学习平台 · 修改邮箱",
           "body": """<html><body style="font-family: sans-serif; max-width: 480px; margin: 0 auto;">
        <h2>📧 修改邮箱</h2>
        <p>您正在把平台账号的邮箱改为这个邮箱，点击下面的链接确认：</p>
        <p style="margin: 20px 0;">
            <a href="{url}" {btn_red}>确认新邮箱</a>
        </p>
        <p style="color: #666; font-size: 14px;">链接 1 小时内有效。</p>
        <p style="color: #999; font-size: 12px;">如果不是您本人操作，请忽略此邮件。</p>
    </body></html>"""},
    "en": {"subject": "AI Development Learning · Email Change",
           "body": """<html><body style="font-family: sans-serif; max-width: 480px; margin: 0 auto;">
        <h2>📧 Email Change</h2>
        <p>You are changing your platform account email to this address. Click below to confirm:</p>
        <p style="margin: 20px 0;">
            <a href="{url}" {btn_red}>Confirm New Email</a>
        </p>
        <p style="color: #666; font-size: 14px;">Link valid for 1 hour.</p>
        <p style="color: #999; font-size: 12px;">If this wasn't you, please ignore this email.</p>
    </body></html>"""},
}


async def send_email_change_email(email: str, token: str, request_base: str = "", lang: str = "en") -> bool:
    """发送改邮箱确认邮件到新邮箱（收信即所有权证明）。"""
    base_url = _resolve_base_url(request_base)
    url = f"{base_url}/email-confirm?token={token}"
    lang = normalize_lang(lang)
    subject, body = _render(EMAIL_CHANGE_TPL, lang, url=url)
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
