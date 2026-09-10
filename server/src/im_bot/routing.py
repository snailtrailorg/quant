"""route_key 单点推导（批13 P0，双盲审 A-P0-2/B-P0-1 同判）。

原三处硬编码白名单 `app_id/client_id/corp_id`（admin 建/自助建/凭证补录）——钉钉
`app_key`、企微 `bot_id` 不在名单 → route 恒空 → 第二只 bot 撞 uq_imbot_provider_route
唯一索引（迁移 0051），补录还会把存量非空 route 改写为空再撞。收口本函数，名单扩展。"""
from __future__ import annotations
import logging

logger = logging.getLogger("im_bot.routing")

# 各平台凭证中的"路由键"字段（平台身份标识，同平台内唯一）：按序取第一个非空值。
# 企微凭证键 bot_id（str）与平台 bot 主键 id（int）不同物——仅在本函数内查 creds dict，不外溢。
ROUTE_KEY_FIELDS = ("app_id", "client_id", "corp_id", "app_key", "bot_id")


def route_key_from(creds: dict) -> str:
    """creds → route_key。（批13 盲审 A-P2：删装饰性 provider 参数——现无跨平台字段
    撞名，通用名单即单真相；新平台字段忘登记时退化为空串与旧实现一致，不更糟。）"""
    if not isinstance(creds, dict):
        return ""
    for f in ROUTE_KEY_FIELDS:
        v = creds.get(f)
        if v:
            return str(v)
    return ""
