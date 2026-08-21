"""飞书对接层——薄壳 re-export(双盲 B-S3:实现已下沉 src/im_bot/feishu_client.py,
本模块保持层 4 入口定位;旧引用零改动)。

3 秒超时约束：Webhook 收到消息立即返回 {"code":0}，LLM 任务丢后台线程。
"""
from src.im_bot.feishu_client import *           # noqa: F401,F403
from src.im_bot.feishu_client import (            # noqa: F401 显式列(非 __all__ 成员)
    FeishuClient, get_feishu_client, evict_feishu_client,
    process_message_async, execute_confirmed_tool,
    check_user, load_feishu_users, FEISHU_USERS,
    verify_event_signature, verify_card_signature, _im_bot_secret,
    build_confirm_card, card_action_fresh,
)
