"""Web 后端 · FastAPI 应用（引导+路由注册）。

启动: uvicorn src.web_api.main:app --reload --port 8000
端点实现全部在 routes/ 各 APIRouter 分组，本文件只做组装；Pydantic 模型在 models.py。
"""

from __future__ import annotations
import os
import logging
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger("web_api")

from .auth import init_users_table, ensure_default_admin
from .errors import ApiError

app = FastAPI(title="量化交易平台 API", version="0.1.0")


@app.exception_handler(ApiError)
async def api_error_handler(request, exc: ApiError):
    """错误码化响应：detail(中文兜底) + 顶层 code（前端 err.<CODE> 本地化）。"""
    from fastapi.responses import JSONResponse
    return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail, "code": exc.code})


from src.feishu_bot.router import router as feishu_router
app.include_router(feishu_router)

# --- 头像静态服务（批次C）：挂 /api/static/avatars -- nginx 已代理 /api/，零额外配置同源可达 ---
# 2026-08-26 3b 修正：头像是运行时数据，位置=shared 层（AVATAR_DIR 环境变量可覆盖）。
# 原 <版本树>/static/avatars 两宗罪：工件化后落在 deploy 属主 releases/<id> 内——
# ① quant mkdir/写入 EACCES（3b-2 首发导入冒烟拦截）；② 与 3b-1 数据外置位不符且逐版丢失。
from pathlib import Path as _Path
from fastapi.staticfiles import StaticFiles as _StaticFiles
_AVATAR_DIR = _Path(os.environ.get("AVATAR_DIR",
                                   "/data/websites/snailtrail.cc/quant/shared/static/avatars"))
try:
    _AVATAR_DIR.mkdir(parents=True, exist_ok=True)   # 服务器：shared 属 quant，服务/冒烟（均 quant）有权
except PermissionError:
    # 开发机回退：无 /data shared 层（权限拒）→ 代码树相对位，保持本地可跑
    _AVATAR_DIR = _Path(__file__).resolve().parents[2] / "static" / "avatars"
    _AVATAR_DIR.mkdir(parents=True, exist_ok=True)
app.mount("/api/static", _StaticFiles(directory=str(_AVATAR_DIR.parent)), name="static")

# CORS（前端 Vue3 开发用）
# SD2（F-58）：CORS 白名单化。默认生产域名；本地 dev 走 vite 同源代理不受影响；
# 跨域开发场景用 CORS_ORIGINS 环境变量覆盖（逗号分隔）
_CORS_ORIGINS = [o.strip() for o in os.environ.get("CORS_ORIGINS", "https://quant.snailtrail.cc").split(",") if o.strip()]
app.add_middleware(
    CORSMiddleware,
    allow_origins=_CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# --- 启动时初始化 ---

@app.on_event("startup")
def startup():
    init_users_table()
    # #48：启动时列级校验（纯函数 -> 入口层路由告警；失败不阻断启动）
    try:
        from src.data_platform.db import verify_schema
        from src.health_monitor.monitor import report_schema_findings
        report_schema_findings(verify_schema())
    except Exception as e:
        logger.warning("startup: schema 校验异常（不阻断）: %s", e)
    if ensure_default_admin():
        print("✓ 创建默认 admin（admin/admin123，请改密码）")
    # 加载自定义因子（因子平台化）
    try:
        from src.strategy_framework.factor import load_factors_from_db
        loaded = load_factors_from_db()
        if loaded:
            print(f"✓ 加载自定义因子: {', '.join(loaded)}")
    except Exception as e:
        logger.warning("startup: 加载自定义因子失败（表可能未创建）: %s", e)


# --- 路由注册（端点实现全部在 routes/ 各 APIRouter，此处只 include） ---

from .routes.system import router as system_router            # /healthz /readyz /metrics /api/help /api/system-config /api/smtp-config 等
from .routes.auth_routes import router as auth_router         # /api/auth/* /api/user* /api/invites /api/log
from .routes.strategy import router as strategy_router        # /api/strategy* /api/factors* /api/live-task
from .routes.trading import router as trading_router          # /api/position /api/pnl /api/orders /api/account /api/dashboard
from .routes.sync import router as sync_router                # /api/sync/* /api/data-source-usage
from .routes.stock import router as stock_router              # /api/stock/* /api/kline /api/screen/*
from .routes.chat import router as chat_router                # /api/chat /ws/chat /ws/market /api/llm-models /api/llm-*
from .routes.im_bots import router as im_bots_router          # /api/im-bots/*
from .routes.alerts import router as alerts_router              # /api/alerts/*（批7 告警订阅）
from .routes.mgmt import router as mgmt_router                # /api/data-sources /api/channels /api/brokers /api/risk-rules /api/tasks
from .routes.risk import router as risk_router                # /api/risk* /api/live-trading /api/reconcile /api/convertible
from .routes.backtest import router as backtest_router        # /api/backtest* /api/pool* /api/broker-usage

app.include_router(system_router)
app.include_router(auth_router)
app.include_router(strategy_router)
app.include_router(trading_router)
app.include_router(sync_router)
app.include_router(stock_router)
app.include_router(chat_router)
app.include_router(im_bots_router)
app.include_router(alerts_router)
app.include_router(mgmt_router)
app.include_router(risk_router)
app.include_router(backtest_router)
