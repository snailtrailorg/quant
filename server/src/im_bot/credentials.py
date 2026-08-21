"""im_bot 凭证统一读写(批 2 读路径切换的唯一入口)。

credentials_encrypted = encrypt(JSON);JSON 内含 FIELD_SCHEMA 全字段
(app_id 明文也入 JSON 保持单真相源,params.route_key 为路由冗余,写入时同步)。
"""
from __future__ import annotations
import json
import logging

logger = logging.getLogger("im_bot.credentials")


def get_bot_credentials(bot_id: int) -> dict:
    """读指定 bot 凭证(解密 JSON)。无行/无凭证/解密失败返回 {}。"""
    from src.data_platform.db import get_conn
    from src.quant_common.crypto import decrypt
    try:
        with get_conn() as conn:
            cur = conn.execute(
                "SELECT provider, credentials_encrypted FROM im_bot_config WHERE id=%s", (bot_id,))
            row = cur.fetchone()
        if not row or not row[1]:
            return {}
        return json.loads(decrypt(row[1]))
    except Exception as e:
        logger.warning("凭证读取失败 bot_id=%s: %s", bot_id, e)
        return {}


def save_bot_credentials(bot_id: int, creds: dict, partial: bool = True) -> bool:
    """写凭证(整 JSON 重加密)。partial=True 时与现值合并(表单只改部分字段)。
    同步维护 params.route_key=creds 里第一个 *id 类字段(飞书=app_id)。
    """
    from src.data_platform.db import get_conn
    from src.quant_common.crypto import encrypt
    if partial:
        creds = {**get_bot_credentials(bot_id), **{k: v for k, v in creds.items() if v}}
    route = creds.get("app_id") or creds.get("client_id") or creds.get("corp_id") or ""
    try:
        with get_conn() as conn:
            has_secret = any(creds.get(k) for k in
                             ("app_secret", "client_secret", "secret", "app_token"))
            conn.execute(
                "UPDATE im_bot_config SET credentials_encrypted=%s, "
                "params = COALESCE(params,'{}'::jsonb) || %s::jsonb, updated_at=now() WHERE id=%s",
                (encrypt(json.dumps(creds, ensure_ascii=False)) if has_secret else None,
                 json.dumps({"route_key": route}), bot_id))
            conn.commit()
        return True
    except Exception as e:
        logger.error("凭证写入失败 bot_id=%s: %s", bot_id, e)
        return False
