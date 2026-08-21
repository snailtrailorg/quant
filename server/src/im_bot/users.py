"""im_bot 用户授权(im_bot_users CRUD,批 2 管理面)。"""
from __future__ import annotations
import logging

logger = logging.getLogger("im_bot.users")

_VALID_ROLES = ("viewer", "analyst", "trader", "admin")


def list_users(bot_id: int) -> list[dict]:
    from src.data_platform.db import get_conn
    with get_conn() as conn:
        cur = conn.execute(
            "SELECT id, im_user_id, role FROM im_bot_users WHERE bot_id=%s ORDER BY im_user_id",
            (bot_id,))
        return [{"id": r[0], "im_user_id": r[1], "role": r[2]} for r in cur.fetchall()]


def upsert_user(bot_id: int, im_user_id: str, role: str) -> dict:
    """新增/改角色(幂等)。返回结果 dict(ApiError 由 web 层抛)。"""
    from src.data_platform.db import get_conn
    if role not in _VALID_ROLES:
        return {"ok": False, "error": f"role 需为 {_VALID_ROLES} 之一"}
    if not im_user_id or not im_user_id.strip():
        return {"ok": False, "error": "im_user_id 必填"}
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO im_bot_users (bot_id, im_user_id, role) VALUES (%s, %s, %s) "
            "ON CONFLICT (bot_id, im_user_id) DO UPDATE SET role=EXCLUDED.role",
            (bot_id, im_user_id.strip(), role))
        conn.commit()
    return {"ok": True}


def delete_user(bot_id: int, im_user_id: str) -> None:
    from src.data_platform.db import get_conn
    with get_conn() as conn:
        conn.execute("DELETE FROM im_bot_users WHERE bot_id=%s AND im_user_id=%s",
                     (bot_id, im_user_id))
        conn.commit()
