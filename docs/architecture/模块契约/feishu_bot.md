# 模块契约 · feishu_bot（飞书/Lark 对接层）

> 本模块的 public API + 依赖 + 被调 + 读写表 + 不变量。任务改本模块前读本文件，不用读整个项目。
> 配套：`docs/architecture/接口契约.md`（跨模块签名 + 数据结构）。本文件不重复数据结构定义，只列"本模块暴露什么"。

## 职责
外部 IM 通道：飞书扫码接入（`register_app`）+ 消息收发（Webhook/长连接）+ AI 动态查询（读类直接执行）+ 紧急处理（操作类发确认卡片等用户确认）。
**3 秒超时铁律**：Webhook 收到消息立即返回 `{"code":0}`，LLM 处理丢后台线程，结果通过飞书"发送消息"API 主动推回。

## 文件结构
```
server/src/feishu_bot/
├── __init__.py        # 重导出 FeishuClient/check_user/process_message_async/build_confirm_card
├── bot.py             # FeishuClient + 鉴权 + 签名 + 后台处理 + 工具执行（读类/操作类）
├── tasks.py           # Celery 任务：feishu_register_task（扫码创建/连接机器人）
├── router.py          # FastAPI APIRouter（/lark/webhook + /lark/card/callback + /lark/test）
└── ws_client.py       # 飞书长连接客户端（lark.ws.Client，systemd quant-feishu-bot 管理）
```

---

## 一、public API（稳定，可跨模块调用）

### bot.py
```python
class FeishuClient:
    def __init__(self)                           # 从 DB feishu_config 读最新 enabled 行凭证（app_id + decrypt(secret)）
    def _get_token(self) -> str                  # tenant_access_token（缓存至 expire-60s）
    def send_text(self, receive_id: str, text: str, receive_id_type: str = "open_id") -> None
    def send_card(self, receive_id: str, card: dict, receive_id_type: str = "open_id") -> None

load_feishu_users() -> list[str] | None          # 从 .env LARK_AUTHORIZED_USERS（user_id:role,...）填 FEISHU_USERS
check_user(open_id: str) -> str | None           # 查 FEISHU_USERS，无则触发 load；返回角色 or None
verify_signature(timestamp: str, body: str, signature: str) -> bool
                                                 # LARK_VERIFICATION_TOKEN 未配则跳过（开发期 True）
build_confirm_card(tool_name: str, args: dict, reason: str = "") -> dict   # 操作确认卡片 JSON
process_message_async(open_id: str, text: str, receive_id_type: str = "open_id",
                      receive_id: str = None, fid: int = None) -> None
    # 后台线程：消息 -> gateway.chat（READ_TOOLS，最多 5 轮 loop）
    # fid 有值 -> per-机器人 role（查 feishu_config.role）；无 fid -> check_user(open_id) 角色映射
    # 读类工具直接 execute_read_tool；操作类发确认卡片后 return（等用户确认）
execute_read_tool(name: str, args: dict) -> str  # query_risk_state/query_strategy_status/query_position/...（position/pnl 待实盘）
execute_confirmed_tool(open_id: str, tool_name: str, args: str) -> None
    # 用户确认后执行操作类：emergency_halt/risk_resume/strategy_stop/strategy_start + audit_log
```

### tasks.py（Celery 任务）
```python
feishu_register_task(self, session_id: str)      # @celery_app.task name="src.feishu_bot.tasks.feishu_register_task"
    # 调 lark.register_app 扫码；on_qr_code 存二维码 base64 到 Valkey
    # 成功 -> 加密 secret 存 feishu_config + Valkey 存 done 状态
```

### router.py（APIRouter，`router`，prefix=`/lark`）
```python
POST /lark/webhook        -> webhook(request)      # 飞书事件订阅回调，3s 内返回 {"code":0}
POST /lark/card/callback  -> card_callback(request) # 卡片按钮回调（confirm/cancel）
GET  /lark/test           -> test_endpoint()        # 测试端点
```

### ws_client.py（长连接，`python -m src.feishu_bot.ws_client [fid]`）
```python
load_feishu_credentials(fid: int | None = None) -> tuple[str, str]   # DB 读 (app_id, decrypt(secret))；无记录抛 RuntimeError
on_message(data) -> None                       # 消息事件 -> process_message_async（复用 bot.py）
main() -> None                                 # 启动 lark.ws.Client（auto_reconnect=True，阻塞）
```

---

## 二、内部 API（不保证稳定）

- `bot.FEISHU_USERS: dict[str, str]`：授权用户表（`load_feishu_users` 填）
- `tasks._set_session(session_id, data, expire=600)`：写 Valkey `feishu:session:{session_id}`（扫码会话状态）
- `ws_client._FID`：当前机器人 id（`main` 从 argv[1] 设，`on_message` 透传给 `process_message_async`）

---

## 三、依赖（import 其他模块什么）

| 本文件 | 依赖 | 用途 |
|---|---|---|
| bot.py | httpx / dotenv（外部） | 飞书 API + .env |
| bot.py | `data_platform.db.get_conn` | 读 feishu_config / strategy_config |
| bot.py | `web_api.crypto_utils.decrypt` | 解密 app_secret |
| bot.py | `llm_gateway.gateway`（gateway/READ_TOOLS/OPERATIONAL_TOOLS） | AI 动态查询 |
| bot.py | `risk_control.RiskControl` / `web_api.auth.audit_log` | 操作类工具执行 + 审计 |
| tasks.py | lark_oapi（lark.register_app）/ redis / qrcode | 扫码 + Valkey 会话 + 二维码图 |
| tasks.py | `scheduler.app`（celery_app）/ `data_platform.db` / `crypto_utils.encrypt` | Celery 注册 + 写 DB + 加密 |
| router.py | fastapi / `bot` | APIRouter + 处理函数 |
| ws_client.py | lark_oapi（lark.ws.Client / EventDispatcherHandler） | 长连接 |

> ⚠️ 循环依赖（同 data_platform）：`feishu_bot` → `web_api.crypto_utils` → `data_platform`；`web_api` 又调 `feishu_bot.router`。靠函数内 import 延迟打破。

---

## 四、被谁调用（改 public API 签名要同步改这些）

| 调用方 | 调什么 |
|---|---|
| `web_api.main` | `from src.feishu_bot.router import router`（include_router）+ `feishu_register_task.delay()`（扫码端点） |
| `scheduler.app` | Celery `include=["src.feishu_bot.tasks"]`（注册 feishu_register_task） |
| systemd `quant-feishu-bot@quant.service` | `python -m src.feishu_bot.ws_client <fid>`（长连接进程） |

> feishu_config 表的 CRUD 端点（list/connect/start/stop/update/delete/test）在 `web_api.main` 直接写 SQL，不经本模块。

---

## 五、读写表

| 表 | 写 | 读 |
|---|---|---|
| `feishu_config` | `tasks.feishu_register_task`（INSERT name/app_id/app_secret_encrypted/role） | `bot.FeishuClient.__init__`（凭证+role）/ `ws_client.load_feishu_credentials` |
| `strategy_config` | — | `bot.execute_read_tool`（query_strategy_status） |
| Valkey `feishu:session:{id}` | `tasks._set_session`（scanning/done/error + 二维码） | web_api 轮询（扫码状态端点） |

> 表 schema：migration 0006（建）+ 0007（加 name）+ 0009（加 role/lang/description）。lang 列 2026-08-07 后不再注入（i18n 简化）。

---

## 六、不变量

- **3 秒超时**：webhook/card_callback 必须立即返回 `{"code":0}`，重活丢 `threading.Thread(daemon=True)`
- **凭证 DB 化**：app_secret Fernet 加密存 `feishu_config.app_secret_encrypted`（弃 .env LARK_*）
- **多机器人**：per-机器人 role（`fid` 查 `feishu_config.role`）；无 fid 走 `.env LARK_AUTHORIZED_USERS`（open_id:role 映射）
- **工具分级**：读类（READ_TOOLS）直接 `execute_read_tool` 执行回填 LLM；操作类（OPERATIONAL_TOOLS）发确认卡片，用户点确认才 `execute_confirmed_tool`
- **send_text 截断**：LLM 回复截 4000 字符（飞书单条限制）
- **长连接优先**：生产用 ws_client（不需公网 webhook），router 仅备用/测试
- **未实现占位**：query_position/query_pnl 返回"待实盘对接"（XTPAdapter 未接入飞书）

---

## 七、扩展指南

### 加新飞书工具（LLM 可调）
1. 读类：`execute_read_tool` 加 `if name == "query_xxx"` 分支（无副作用，直接返回字符串）
2. 操作类：`execute_confirmed_tool` 加 `elif tool_name == "xxx"` 分支 + 走确认卡片流程
3. 工具定义在 `llm_gateway.gateway`（READ_TOOLS / OPERATIONAL_TOOLS），不改本模块

### 加新消息通道（钉钉/企微）
- 不在本模块扩展，走平台化 `MessageChannel` 接口（`alert_notify.channel`）；本模块是飞书专用对接层

---

## 修订记录
- 2026-08-10 初版（基于代码核实：bot/tasks/router/ws_client 全读 + 被调/表 grep）
