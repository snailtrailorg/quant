# 模块契约 · feishu_bot（飞书/Lark 入口层）

> 本模块的 public API + 依赖 + 被调 + 读写表 + 不变量。任务改本模块前读本文件，不用读整个项目。
> 配套：`docs/architecture/接口契约.md`（跨模块签名 + 数据结构）；**`模块契约/im_bot.md`**（实现层，19 号 IM 统一接入）。

## 职责
飞书通道**入口层**（层 4）：Webhook/长连接收发入口 + Celery 扫码接入任务。
**实现已下沉 `src/im_bot/`**（19 号 IM 统一接入，2026-08-21 批 1+2；双盲 B-S3 分层修正：层 3 服务层不得被 im_bot 反向 import）——`bot.py` 现为 re-export 薄壳，旧引用零改动。接新 IM（钉钉/企微）不再动本模块，走 `im_bot.IMBotProvider`（见下「扩展指南」）。
**3 秒超时铁律**：Webhook 收到消息立即返回 `{"code":0}`，LLM 处理丢后台线程，结果通过飞书"发送消息"API 主动推回。

## 文件结构
```
server/src/feishu_bot/
├── __init__.py        # 重导出 FeishuClient/check_user/process_message_async/build_confirm_card
├── bot.py             # 薄壳 re-export（13 行）——实现全在 im_bot/feishu_client.py（B-S3 下沉）
├── tasks.py           # Celery 任务：feishu_register_task（扫码创建/连接机器人，存 im_bot_config）
├── router.py          # FastAPI APIRouter（/lark/webhook + /lark/card/callback + /lark/test）
└── ws_client.py       # 飞书长连接客户端（lark.ws.Client，systemd quant-feishu-bot@ 管理）
```
> 实现层 `server/src/im_bot/`（5 文件，契约见 `模块契约/im_bot.md`）：`base.py`（IMBotProvider 抽象+注册表）/ `feishu.py`（FeishuProvider 适配，FIELD_SCHEMA 4 字段）/ `feishu_client.py`（客户端/签名/授权/消息处理，自本模块 bot.py 下沉）/ `credentials.py`（凭证 JSON 统一读写）/ `users.py`（im_bot_users CRUD）。

---

## 一、public API（稳定，可跨模块调用）

### bot.py（薄壳 re-export；实现 `im_bot/feishu_client.py`，2026-08-21 批 2 下沉）
```python
class FeishuClient:
    def __init__(self, bot_id: int | None = None)   # 凭证读 im_bot_config（bot_id=None=最新 enabled 行；
                                                     #   creds 经 im_bot.credentials.get_bot_credentials 解密 JSON）
    def _get_token(self) -> str                     # tenant_access_token（缓存至 expire-60s）
    def send_text(self, receive_id, text, receive_id_type="open_id") -> None
    def send_card(self, receive_id, card, receive_id_type="open_id") -> None

get_feishu_client(bot_id: int | None = None) -> FeishuClient   # （新增 批2）per-bot 单例，TTL 300s
    # 修两隐患：多 bot 回复走错凭证 / 每消息 new 实例 token 缓存形同虚设；凭证热更新最多 5min 生效
evict_feishu_client(bot_id: int | None) -> None    # （新增 批2）凭证写路径后主动失效（同进程）

load_feishu_users() -> list[str] | None            # env LARK_AUTHORIZED_USERS——兜底层（主真相源=im_bot_users 表）
check_user(open_id: str) -> str | None             # 先查 im_bot_users（全部 feishu bot 行并集，角色取最高），
                                                   #   查无回落 env 层，再无=None（fail-closed）
verify_event_signature(header_ts, nonce, body, signature) -> bool
    # sha256(ts+nonce+EncryptKey+body)（官方算法，P0 复审修正）；密钥主源 im_bot_config.credentials.encrypt_key，
    #   env 兜底；未配置 Encrypt Key 跳过（纯 token 模式兼容）
verify_card_signature(header_ts, nonce, body, signature) -> bool
    # sha1(ts+nonce+VerificationToken+body)；密钥主源 im_bot_config.credentials.verification_token，
    #   表+env 皆空=fail-closed 拒（卡片是操作执行面，P0-2）
build_confirm_card(tool_name: str, args: dict, reason: str = "") -> dict   # 确认卡片，按钮 value 携带 ts
card_action_fresh(value: dict, max_age_s: int = 60) -> bool               # （新增 SD2/F-33）60s 时效防重放
process_message_async(open_id: str, text: str, receive_id_type: str = "open_id",
                      receive_id: str = None, fid: int = None) -> None
    # 后台线程：消息 -> gateway.chat（READ_TOOLS，轮次 system_config.llm_max_tool_turns 默认 5）
    # role：fid 有值 -> per-机器人（im_bot_config.default_role）；无 fid -> check_user(open_id)
    # 读类工具直接 execute_read_tool；操作类发确认卡片后 return（等用户确认）
execute_read_tool(name: str, args: dict) -> str  # query_risk_state/query_strategy_status/query_position/...（position/pnl 待实盘）
execute_confirmed_tool(open_id: str, tool_name: str, args: str) -> None
    # 用户确认后执行操作类：emergency_halt/risk_resume/strategy_stop/strategy_start + data_platform.audit 审计
```

### tasks.py（Celery 任务）
```python
feishu_register_task(self, session_id: str)      # @celery_app.task name="src.feishu_bot.tasks.feishu_register_task"
    # 调 lark.register_app 扫码；on_qr_code 存二维码 base64 到 Valkey
    # 成功 -> 凭证加密存 im_bot_config（批 2）：同 route_key(app_id) 重扫=UPDATE 合并凭证
    #   （save_bot_credentials partial，保已有 token/ek），不再堆重复行（修批 1 审计 A-S1）
    # 新行 INSERT (provider='feishu', default_role='viewer', params.route_key=app_id)
```

### router.py（APIRouter，`router`，prefix=`/lark`）
```python
POST /lark/webhook        -> webhook(request)      # 飞书事件订阅回调，3s 内返回 {"code":0}
                                                   #   线程池 _executor(10) 提交 process_message_async
POST /lark/card/callback  -> card_callback(request) # 卡片按钮回调（confirm/cancel），三道闸：
                                                   #   ①验签 fail-closed（表+env 皆空拒）②event_id 5min 去重
                                                   #   ③60s 时效 + 角色门槛（trader/admin；risk_resume 仅 admin）
GET  /lark/test           -> test_endpoint()        # 测试端点
```

### ws_client.py（长连接，`python -m src.feishu_bot.ws_client [fid]`）
```python
load_feishu_credentials(fid: int | None = None) -> tuple[str, str]   # DB 读 (app_id, secret)——经
    # im_bot.credentials.get_bot_credentials（fid 指定行；None=最新 enabled feishu 行）；无凭证抛 RuntimeError
on_message(data) -> None                       # 消息事件 -> process_message_async（复用 im_bot/feishu_client）
main() -> None                                 # 启动 lark.ws.Client（auto_reconnect=True，阻塞）
```

---

## 二、内部 API（不保证稳定）

- `bot.FEISHU_USERS: dict[str, str]`（实现于 feishu_client）：env 兜底授权层（`load_feishu_users` 填）——主真相源=im_bot_users 表（批 1 双轨过渡，批 2 起表为主）
- `tasks._set_session(session_id, data, expire=600)`：写 Valkey `feishu:session:{session_id}`（扫码会话状态）
- `ws_client._FID`：当前机器人 id（`main` 从 argv[1] 设，`on_message` 透传给 `process_message_async`）

---

## 三、依赖（import 其他模块什么）

| 本文件 | 依赖 | 用途 |
|---|---|---|
| bot.py | `im_bot.feishu_client`（re-export，零逻辑） | 实现层（B-S3 下沉后本文件无直接依赖） |
| router.py | fastapi / `bot`（即 im_bot.feishu_client） | APIRouter + 处理函数 + redis（卡片 event_id 去重） |
| tasks.py | lark_oapi（lark.register_app）/ redis / qrcode | 扫码 + Valkey 会话 + 二维码图 |
| tasks.py | `scheduler.app`（celery_app）/ `data_platform.db` / `quant_common.crypto.encrypt` / `im_bot.credentials` | Celery 注册 + 写 im_bot_config + 加密 |
| ws_client.py | lark_oapi（lark.ws.Client / EventDispatcherHandler）/ `im_bot.credentials` | 长连接 + 凭证读取 |
| （实现层 feishu_client） | httpx / `data_platform.db` / `im_bot.credentials` / `llm_gateway`（gateway/READ_TOOLS/OPERATIONAL_TOOLS）/ `risk_control.RiskControl` / `data_platform.audit` | 飞书 API + LLM 动态查询 + 操作类工具 + 审计 |

> （2026-08-27 回写：原"feishu_bot → web_api.crypto_utils → data_platform 循环依赖"已两次消除——2026-08-19 crypto 归位 quant_common（层 0），2026-08-21 批 2 实现下沉 im_bot。分层断言 `tests/test_layering.py`：im_bot（层 3）零上行边。）

---

## 四、被谁调用（改 public API 签名要同步改这些）

| 调用方 | 调什么 |
|---|---|
| `web_api.main` | `from src.feishu_bot.router import router`（include_router） |
| `web_api.routes.im_bots` | `feishu_register_task.delay()`（onboarding 扫码端点；`/api/im-bots` 端点群：providers/CRUD/start/stop/test/users——直接 SQL+im_bot 模块，不经本模块） |
| `scheduler.app` | Celery `include=["src.feishu_bot.tasks"]`（注册 feishu_register_task） |
| systemd `quant-feishu-bot@quant.service` | `python -m src.feishu_bot.ws_client <fid>`（长连接进程） |

> （2026-08-27 回写：原"feishu_config CRUD 端点在 web_api.main 直接写 SQL"已过时——表已删（0052），机器人 CRUD 迁 `web_api/routes/im_bots.py` 的 `/api/im-bots` 端点群，走 im_bot 模块。）

---

## 五、读写表

| 表 | 写 | 读 |
|---|---|---|
| `im_bot_config` | `tasks.feishu_register_task`（扫码 INSERT/UPDATE，凭证经 `im_bot.credentials.save_bot_credentials` 整 JSON 加密） | `FeishuClient.__init__` / `_im_bot_secret`（签名密钥）/ `process_message_async`（default_role）/ `ws_client.load_feishu_credentials` |
| `im_bot_users` | —（写经 `im_bot.users`，web 端点） | `check_user`（授权并集 JOIN im_bot_config） |
| `strategy_config` | — | `execute_read_tool`（query_strategy_status） |
| Valkey `feishu:session:{id}` | `tasks._set_session`（scanning/done/error + 二维码） | web_api 轮询（onboarding-status 端点） |
| Valkey `feishu:card:{event_id}` | `router.card_callback`（NX EX 300s 去重） | 同左（防重放） |

> 表 schema：`im_bot_config` + `im_bot_users` = migration **0051**（建两表 + **feishu_config 全列数据迁移**，密文容错解密）；**0052 DROP feishu_config**（批 2 切完全部读路径后）。feishu_config 的 lang 列注入早已废弃（i18n 简化）。

---

## 六、不变量

- **3 秒超时**：webhook/card_callback 必须立即返回 `{"code":0}`，重活丢线程池/`threading.Thread(daemon=True)`
- **凭证 DB 化**：凭证 JSON 整串 Fernet 加密存 `im_bot_config.credentials_encrypted`（FIELD_SCHEMA 全字段单真相源；全空=NULL）；`params.route_key` 与凭证 id 字段同写（唯一索引 (provider, route_key) 防漂移）
- **per-bot 单例**：`get_feishu_client(fid)` TTL 300s——凭证热更新最多 5 分钟生效，即时生效走 start/stop 端点重启进程
- **多机器人**：per-机器人 role（`fid` 查 `im_bot_config.default_role`）；无 fid 走 `check_user`（im_bot_users → env 兜底）
- **签名密钥主源**：`im_bot_config.credentials`（encrypt_key / verification_token），env 兜底；**卡片路径 fail-closed**（表+env 皆空即拒——操作执行面）
- **卡片三道闸**：验签 + event_id 5min 去重 + 60s 时效；操作类另加角色门槛（trader/admin，risk_resume 仅 admin）
- **工具分级**：读类（READ_TOOLS）直接 `execute_read_tool` 执行回填 LLM；操作类（OPERATIONAL_TOOLS）发确认卡片，用户点确认才 `execute_confirmed_tool`
- **send_text 截断**：LLM 回复截 4000 字符（飞书单条限制）
- **长连接优先**：生产用 ws_client（不需公网 webhook），router 仅备用/测试；MODE=hybrid（消息 ws + 卡片 webhook）
- **未实现占位**：query_position/query_pnl 返回"待实盘对接"（XTPAdapter 未接入飞书）

---

## 七、扩展指南

### 加新飞书工具（LLM 可调）
1. 读类：`execute_read_tool` 加 `if name == "query_xxx"` 分支（无副作用，直接返回字符串）
2. 操作类：`execute_confirmed_tool` 加 `elif tool_name == "xxx"` 分支 + 走确认卡片流程
3. 工具定义在 `llm_gateway.gateway`（READ_TOOLS / OPERATIONAL_TOOLS），不改本模块

### 加新 IM 平台（钉钉/企微，19 号 IM 统一接入）
- **不在本模块扩展**（本模块=飞书专用入口）。路线：`src/im_bot/` 实现 `IMBotProvider` 子类（FIELD_SCHEMA 凭证声明/MODE/ONBOARDING/send_text/send_card/verify_callback/test_connection）+ `register_provider` 注册 + locales 加字段词条 + DB `im_bot_config` 配一行——**平台代码零改动**
- 与 `alert_notify.channel` 的 MessageChannel 区别：那是单向告警出站；IMBotProvider 是完整 IM 接入面（收发+确认卡片+接入向导）
- 详见 `docs/architecture/19-IM统一接入设计.md` + `模块契约/im_bot.md`

---

## 修订记录
- 2026-08-10 初版（基于代码核实：bot/tasks/router/ws_client 全读 + 被调/表 grep）
- 2026-08-27 回写（19 号 IM 统一接入批 1+2，2026-08-21 已上线）：bot.py 实现下沉 `im_bot/feishu_client.py`（本模块变薄壳入口层）；feishu_config → im_bot_config+im_bot_users（0051 迁移/0052 删表）；补 get_feishu_client/evict_feishu_client/card_action_fresh 新 API、签名官方算法+fail-closed、卡片三道闸、循环依赖两度消除；扩展指南改 IMBotProvider 路线

## 最近变更
- 2026-08-27 契约回写：补 19 号 IM 统一接入后的职责变化（实现层 im_bot / 统一表 / 凭证 JSON / 动态 FIELD_SCHEMA / 接入向导）——代码本身 2026-08-21 批 1+2 已上线，本文件此前未跟
