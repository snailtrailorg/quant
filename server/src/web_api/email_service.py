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


async def send_invite_email(email: str, token: str, base_url: str = "") -> bool:
    """发送邀请开通邮件。"""
    base_url = base_url or os.environ.get("BASE_URL", "https://120.24.235.98")
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
        <p style="color: #666; font-size: 14px;">链接 7 天内有效。开通后默认 Viewer 角色，管理员可提升权限。</p>
        <p style="color: #999; font-size: 12px;">如果不是您本人操作，请忽略此邮件。</p>
    </body></html>
    """
    return await send_email(email, subject, body)


async def send_password_reset_email(email: str, token: str, base_url: str = "") -> bool:
    """发送密码重置邮件。"""
    base_url = base_url or os.environ.get("BASE_URL", "https://120.24.235.98")
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
