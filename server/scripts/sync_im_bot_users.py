#!/usr/bin/env python
"""LARK_AUTHORIZED_USERS env → im_bot_users 导入(IM 统一接入批 1 配套,19 号 v2 §6)。

幂等:ON CONFLICT (bot_id, im_user_id) DO UPDATE。导入到全部 enabled 的 feishu bot 行
(webhook 无 per-bot 路由的批 1 语义)。删除语义=表为准(env 里删掉的用户 DB 残留不管)。

用法(服务器,venv 环境):
    cd /data/websites/snailtrail.cc/quant/server && venv/bin/python -m scripts.sync_im_bot_users
本地: cd server && JWT_SECRET=... venv/bin/python -m scripts.sync_im_bot_users
"""
import os
import sys

from dotenv import load_dotenv

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
load_dotenv(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env"))

VALID_ROLES = {"viewer", "analyst", "trader", "admin"}


def main() -> int:
    raw = os.environ.get("LARK_AUTHORIZED_USERS", "")
    users = {}
    for pair in raw.split(","):
        if ":" in pair:
            uid, role = pair.strip().split(":", 1)
            if role in VALID_ROLES:
                users[uid] = role
            else:
                print(f"跳过非法角色: {uid}:{role}")
    if not users:
        print("LARK_AUTHORIZED_USERS 为空——无导入(表保持现状)")
        return 0

    from src.data_platform.db import get_conn
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT id FROM im_bot_config WHERE provider='feishu' AND enabled").fetchall()
        if not rows:
            print("无 enabled feishu bot 行——先跑迁移 0051 并确认数据")
            return 1
        n = 0
        for (bid,) in rows:
            for uid, role in users.items():
                conn.execute(
                    "INSERT INTO im_bot_users (bot_id, im_user_id, role) VALUES (%s, %s, %s) "
                    "ON CONFLICT (bot_id, im_user_id) DO UPDATE SET role=EXCLUDED.role",
                    (bid, uid, role))
                n += 1
        conn.commit()
    print(f"导入完成: {len(users)} 用户 × {len(rows)} bot = {n} 行(幂等)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
