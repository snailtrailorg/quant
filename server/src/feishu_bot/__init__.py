"""飞书/Lark 对接层 —— AI 动态查询 + 紧急处理（带确认）。

挂载到 FastAPI: from src.feishu_bot.router import router; app.include_router(router)
"""
from .bot import FeishuClient, check_user, process_message_async, build_confirm_card
