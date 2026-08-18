# 2026-08-19 模块归位：terms 注册表迁 quant_common（email_service 跨层消费——纯数据无业务依赖）
from src.quant_common.terms import *  # noqa: F401,F403
from src.quant_common.terms import get_terms, get_terms_items, available_langs, LANG_NAMES, TERMS  # noqa: F401
