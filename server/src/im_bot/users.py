"""im_bot 用户授权(im_bot_users CRUD,批 2 管理面)。"""
from __future__ import annotations
import logging

logger = logging.getLogger("im_bot.users")

_VALID_ROLES = ("viewer", "analyst", "trader", "admin")


def backfill_from_env(bot_id: int) -> int:
    """env 授权层一次性回填 im_bot_users（2026-09-02：19 号双轨收尾——表为主真相源，
    env 扫码时代的 LARK_AUTHORIZED_USERS 从未迁入，聊天靠 check_user 兜底活着而告警
    dispatch 只读表误判"无绑定"）。表非空=no-op（幂等）；角色非法回落 viewer。
    返回回填行数。"""
    try:
        if list_users(bot_id):
            return 0
        # 多 bot 语义护栏（2026-09-02 用户裁定 .env 为待废弃残留、平台走多 bot）：
        # env 授权层属"原始单 bot 时代"，仅当目标 bot 是唯一启用的 feishu bot 时回填——
        # 多 bot 共存即语义不明（env 用户不该自动授权第二个 bot），跳过留人工管理。
        from src.data_platform.db import get_conn
        with get_conn() as conn:
            cur = conn.execute(
                "SELECT count(*) FROM im_bot_config WHERE provider='feishu' AND enabled")
            if (cur.fetchone()[0] or 0) != 1:
                logger.info("backfill_from_env(%s) 跳过：启用的 feishu bot ≠1（多 bot 时代 env 残留不迁移）", bot_id)
                return 0
        from .feishu_client import load_feishu_users
        env_users = load_feishu_users()
        if not env_users:
            return 0
        n = 0
        for open_id, role in env_users.items():
            r = upsert_user(bot_id, open_id, role if role in _VALID_ROLES else "viewer")
            if r.get("ok"):
                n += 1
        if n:
            logger.info("im_bot_users 回填 bot=%s ← env 授权层 %d 行（19 号双轨收尾）", bot_id, n)
        return n
    except Exception as e:
        logger.warning("backfill_from_env(%s) 失败（不影响主流程）: %s", bot_id, e)
        return 0


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
