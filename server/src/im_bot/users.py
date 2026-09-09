"""im_bot 用户授权(im_bot_users CRUD,批 2 管理面)。"""
from __future__ import annotations
import logging

logger = logging.getLogger("im_bot.users")

_VALID_ROLES = ("viewer", "analyst", "trader", "admin")


def backfill_from_env(bot_id: int) -> int:
    """env 授权层一次性回填 im_bot_users（2026-09-02：arch-19双轨收尾——表为主真相源，
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
            logger.info("im_bot_users 回填 bot=%s ← env 授权层 %d 行（arch-19双轨收尾）", bot_id, n)
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


def upsert_user(bot_id: int, im_user_id: str, role: str, user_id: int | None = None) -> dict:
    """新增/改绑定(幂等)。批11C：user_id=绑定平台账号（管理面通道）；校验账号存在且活跃。

    注意 user_id=None 语义=**清除绑定**回留痕（role 列已不参与授权,见 resolve_im_identity）。"""
    from src.data_platform.db import get_conn
    if role not in _VALID_ROLES:
        return {"ok": False, "error": f"role 需为 {_VALID_ROLES} 之一"}
    if not im_user_id or not im_user_id.strip():
        return {"ok": False, "error": "im_user_id 必填"}
    if user_id is not None:
        with get_conn() as conn:
            if not conn.execute(
                    "SELECT 1 FROM users WHERE id=%s AND enabled AND deleted_at IS NULL",
                    (user_id,)).fetchone():
                return {"ok": False, "error": f"用户 {user_id} 不存在或已停用"}
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO im_bot_users (bot_id, im_user_id, role, user_id) VALUES (%s, %s, %s, %s) "
            "ON CONFLICT (bot_id, im_user_id) DO UPDATE SET role=EXCLUDED.role, user_id=EXCLUDED.user_id",
            (bot_id, im_user_id.strip(), role, user_id))
        conn.commit()
    return {"ok": True}


def delete_user(bot_id: int, im_user_id: str) -> None:
    from src.data_platform.db import get_conn
    with get_conn() as conn:
        conn.execute("DELETE FROM im_bot_users WHERE bot_id=%s AND im_user_id=%s",
                     (bot_id, im_user_id))
        conn.commit()


def resolve_im_identity(open_id: str) -> dict | None:
    """批11C：open_id → 绑定的平台账号（身份源唯一——聊天/卡片全路径）。

    fail-closed 语义（方案 v2 双盲审 A-P0-1/P1-2/P2-1）：
    - 仅 user_id 非空行算绑定（首见留痕行=NULL 不获任何权限）
    - 多 bot 多行 user_id 不一致 → 拒（多义 fail-closed）
    - 绑定账号停用/软删 → 拒（join users 校验 enabled+deleted_at）
    - role 列回落已删、env 兜底已从身份面摘除（告警 backfill 用途保留）
    返回 {user_id, username, role, perms} 或 None。
    """
    from src.data_platform.db import get_conn
    try:
        with get_conn() as conn:
            rows = conn.execute(
                "SELECT DISTINCT u.user_id FROM im_bot_users u "
                "JOIN im_bot_config b ON b.id = u.bot_id "
                "WHERE u.im_user_id = %s AND u.user_id IS NOT NULL "
                "AND b.provider = 'feishu' AND b.enabled", (open_id,)).fetchall()
        uids = {r[0] for r in rows}
        if not uids:
            return None
        if len(uids) > 1:
            logger.warning("open_id 多义绑定（跨 bot 不同账号）fail-closed: %s…", open_id[:10])
            return None
        uid = uids.pop()
        with get_conn() as conn:
            r = conn.execute(
                "SELECT username, role FROM users WHERE id = %s AND enabled AND deleted_at IS NULL",
                (uid,)).fetchone()
        if not r:
            logger.warning("绑定账号已停用/软删 fail-closed: user_id=%s", uid)
            return None
        from src.data_platform.perms import load_effective_permissions
        perms, _ = load_effective_permissions(r[0], r[1])
        return {"user_id": uid, "username": r[0], "role": r[1], "perms": perms}
    except Exception as e:
        logger.warning("身份解析失败 fail-closed: %s", e)
        return None


def bind_owner(bot_id: int, open_id: str, user_id: int) -> dict:
    """绑定 IM 身份到平台账号（批11C 自助面——user_id 服务端钉死，调用方传认证会话值）。"""
    from src.data_platform.db import get_conn
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO im_bot_users (bot_id, im_user_id, role, user_id) VALUES (%s,%s,'viewer',%s) "
            "ON CONFLICT (bot_id, im_user_id) DO UPDATE SET user_id=EXCLUDED.user_id",
            (bot_id, open_id, user_id))   # 批11C（B-P2-7）：ON CONFLICT 原子化（并发首见留痕不再裸 500）
        conn.commit()
    return {"ok": True}
