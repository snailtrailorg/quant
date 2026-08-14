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


def _send_email_sync(to: str, subject: str, html_body: str) -> bool:
    """底层同步发送邮件。"""
    if not os.environ.get("SMTP_USERNAME"):
        print(f"[DEV] 邮件未配置 -> {to}\n[DEV] 主题={subject}\n[DEV] 内容={html_body}")
        return True

    smtp_from = os.environ.get("SMTP_FROM")
    smtp_host = os.environ.get("SMTP_HOST")
    smtp_port = os.environ.get("SMTP_PORT", "587")
    if not smtp_from or not smtp_host:
        _logger.warning("SMTP_FROM 或 SMTP_HOST 未配置，跳过邮件发送")
        return False

    msg = MIMEMultipart()
    msg["From"] = smtp_from
    msg["To"] = to
    msg["Subject"] = subject
    msg.attach(MIMEText(html_body, "html"))

    try:
        with smtplib.SMTP(smtp_host, int(smtp_port)) as server:
            server.starttls()
            server.login(os.environ["SMTP_USERNAME"], os.environ["SMTP_PASSWORD"])
            server.sendmail(smtp_from, to, msg.as_string())
        return True
    except (smtplib.SMTPException, OSError) as e:
        _logger.exception(f"Email send failed: {e}")
        return False


async def send_email(to: str, subject: str, html_body: str) -> bool:
    """异步发送邮件（run_in_executor 不阻塞 FastAPI）。"""
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, _send_email_sync, to, subject, html_body)


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


async def send_invite_email(email: str, token: str, request_base: str = "") -> bool:
    """发送邀请开通邮件。"""
    base_url = _resolve_base_url(request_base)
    register_url = f"{base_url}/register?token={token}"
    subject = "量化交易平台 · 邀请开通"
    body = f"""
    <html><body style="font-family: sans-serif; max-width: 480px; margin: 0 auto;">
        <h2>📧 邀请开通</h2>
        <p>您被邀请开通量化交易平台账号。</p>
        <p style="margin: 20px 0;">
            <a href="{register_url}" style="display: inline-block; padding: 12px 24px;
               background: #409eff; color: white; text-decoration: none; border-radius: 6px;">
               点击开通账号</a>
        </p>
        <p style="color: #666; font-size: 14px;">链接 3 天内有效。开通后默认 Viewer 角色，管理员可提升权限。</p>
        <p style="color: #999; font-size: 12px;">如果不是您本人操作，请忽略此邮件。</p>
    </body></html>
    """
    # 邀请邮件审计日志（who/ base_url/ 链接），便于排查"链接不对"
    _logger.info("send invite email: to=%s base_url=%s register_url=%s", email, base_url, register_url)
    return await send_email(email, subject, body)


async def send_password_reset_email(email: str, token: str, request_base: str = "") -> bool:
    """发送密码重置邮件。"""
    base_url = _resolve_base_url(request_base)
    reset_url = f"{base_url}/reset-password?token={token}"
    subject = "量化交易平台 · 密码重置"
    body = f"""
    <html><body style="font-family: sans-serif; max-width: 480px; margin: 0 auto;">
        <h2>🔐 密码重置</h2>
        <p>您请求重置密码。</p>
        <p style="margin: 20px 0;">
            <a href="{reset_url}" style="display: inline-block; padding: 12px 24px;
               background: #f56c6c; color: white; text-decoration: none; border-radius: 6px;">
               点击重置密码</a>
        </p>
        <p style="color: #666; font-size: 14px;">链接 1 小时内有效。</p>
        <p style="color: #999; font-size: 12px;">如果不是您本人操作，请忽略此邮件。</p>
    </body></html>
    """
    return await send_email(email, subject, body)


async def send_activation_email(email: str, username: str, request_base: str = "") -> bool:
    """开通成功通知邮件：附登录链接 +《平台使用条款》全文。"""
    from .terms import TERMS_ZH
    base_url = _resolve_base_url(request_base)
    login_url = f"{base_url}/login"
    # 条款 pre-wrap 渲染（HTML 转义换行）
    terms_html = TERMS_ZH.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace("\n", "<br>")
    subject = "账号已开通 · 人工智能开发学习平台"
    body = f"""
    <html><body style="font-family: sans-serif; max-width: 560px; margin: 0 auto;">
        <h2>✅ 账号开通成功</h2>
        <p>您的账号已开通，可登录使用。</p>
        <p>用户名：<b>{username}</b><br/>
           初始权限：Viewer（只读，可查看持仓/盈亏/状态等）。交易及管理权限不向受邀用户开放。</p>
        <p style="margin: 20px 0;">
            <a href="{login_url}" style="display: inline-block; padding: 12px 24px;
               background: #409eff; color: white; text-decoration: none; border-radius: 6px;">点击登录</a>
        </p>
        <hr/>
        <p style="color: #666; font-size: 14px;">以下是《平台使用条款》，开通即视为您已阅读并同意：</p>
        <div style="font-size: 12px; color: #606266; line-height: 1.7; white-space: pre-wrap;">{terms_html}</div>
    </body></html>
    """
    _logger.info("send activation email: to=%s username=%s base_url=%s", email, username, base_url)
    return await send_email(email, subject, body)
