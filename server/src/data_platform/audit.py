"""审计日志（从 web_api/auth.py 下沉，2026-08-19 P 审——纯 DB INSERT 属数据层；
web_api.feishu_bot 曾因此反向 import 顶层）。"""
from __future__ import annotations


def audit_log(actor: str, action: str, target: str = "", detail: str = "",
              old_value: str = "", new_value: str = ""):
    """写审计日志（含新旧值对比）。"""
    from .db import get_conn
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO audit_log (actor, action, target, detail, old_value, new_value) "
            "VALUES (%s,%s,%s,%s,%s,%s)",
            (actor, action, target, detail, old_value, new_value),
        )
        conn.commit()
