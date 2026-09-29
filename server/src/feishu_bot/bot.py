"""飞书对接层——薄壳 re-export(双盲 B-S3:实现已下沉 src/im_bot/feishu_client.py,
本模块保持层 4 入口定位;旧引用零改动)。

3 秒超时约束：Webhook 收到消息立即返回 {"code":0}，LLM 任务丢后台线程。
"""
from src.im_bot.feishu_client import *  # noqa: F401,F403
from src.im_bot.feishu_client import (  # noqa: F401 显式列(非 __all__ 成员)
    FEISHU_USERS,
    FeishuClient,
    _im_bot_secret,
    build_confirm_card,
    card_action_fresh,
    evict_feishu_client,
    execute_confirmed_tool,
    get_feishu_client,
    load_feishu_users,
    process_message_async,
    verify_event_signature,  # 批29-4：verify_card_signature 退役（HTTP 卡片面桩化）
)
