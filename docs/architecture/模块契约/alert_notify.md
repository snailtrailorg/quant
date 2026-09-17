# 模块契约 · alert_notify（告警/通知）

> 本模块的 public API + 依赖 + 被调 + 读写表 + 不变量。任务改本模块前读本文件，不用读整个项目。
> 配套：`docs/architecture/接口契约.md`（跨模块签名 + 数据结构）。本文件不重复数据结构定义，只列"本模块暴露什么"。

## 职责
统一告警出口：分级路由（info/warn/critical）+ 1min 去重合并 + 日配额限流 + 历史记录。
所有模块通过 `AlertNotify.get().notify(level, title, body)` 推送，不直接接触渠道。渠道走 `MessageChannel` 抽象（PT1 平台化，DB 配置 `channel_config`，别人实现接口接入）。

## 文件结构
```
server/src/alert_notify/
├── notify.py     # AlertNotify 单例（分级路由 + 去重 + 配额 + 记录）；批39 report() 改走订阅广播（旧 webhook 直推退役）
├── dispatch.py   # （channel.py 已删——批39 webhook 链连根退役：channel_config 表 0085 drop、/api/channels 五端点删、MessageChannel 抽象只剩平台化接口声明）
├── dispatch.py   # 批7 订阅分发（三通道 Celery 三队列+claim/writeback 审计；批30 订阅用户化：_load_channels 读 alert_user_sub 展开为用户三通道行——行契约 {id,sub_id}，email/sms 行 id=user_id、im 行 id=bot_id 保 (ch,id) 唯一；软删停用 JOIN 过滤；legacy webhook 外推分支已退役）
├── sms.py        # 批7 阿里云短信（dysmsapi 签名 V1 零 SDK）；批30 加 send_sms_code（验证码模板键 alert_sms_verify_template_code 独立读，不入 _CFG_KEYS——防告警面被误伤）
└── __init__.py   # 导出 AlertNotify
```

> **批30（2026-09-16）**：订阅维度=用户（`alert_user_sub` 表）——被选用户的邮箱/手机/名下全部 enabled bot 即投递目标（可用就发不可用跳过；多 bot 全发=用户裁定）。旧表 `alert_channel_sub` 保留不读写（im 行存量已迁 owner 用户行，email/sms 行 legacy 清单提示重建）。worker 侧重查 `_still_enabled` 走 sub_id 查新表（scheduler/alert_tasks.py）。已知修：批30 盲审 B-P0 发现 `_send_im` SQL 内嵌 `#` 注释致 IM 告警自批11C 起静默失效——已修（注释移出字符串）。

> **批34（2026-09-17）**：通道级勾选——`alert_user_sub.channels` JSONB 三态（NULL=全通道自动/`[]`=零通道静音/非空=按勾选 `["email","sms","im:3"]` im 粒度到 bot）；`_ch_ok(sel,key)=sel is None or key in sel`（禁 `not sel`——`[]` 假值全开）；GET 行 `channels_sel`（剥离失效键）/`channels_avail`；PUT 缺 channels 键=沿用现值；实体消失键剥离落库、实体在但不属用户/畸形键 400 `ALERT_CHANNEL_INVALID`。
> **批40（2026-09-17 深夜）Channel Profile 注册表（内容适配层——用户裁定建框架）**：`dispatch.py` 三通道画像 `CHANNEL_PROFILES`（dataclass：content_model full|templated + supports_body + max_title + renderer + sender）；通知上下文 ctx={level,category,title,body,code,notif_id} 全通道同源 → 画像渲染（`_render_full`/`_render_email`/`_render_sms`）→ 传输层纯发（三 sender keyword-only 签名统一，**title[:20] 截断从 sms.py 传输层上移渲染层**——「短信=敲门媒介」产品裁定成显式契约）。加新通道=注册表加一条（渲染+传输各一函数），`_send_one` 零改动；未知通道 not_configured 拒。test 端点同源走 `_render_sms`。

> **批39（2026-09-17 全局检视）**：**webhook 链连根退役**（channel.py/五端点/ChannelReq/0085 drop channel_config）；盘后报告 `report()` 改走 `dispatch.broadcast()`——跳 min_level 门槛的订阅推送（报告=订阅意图明确的常规通知非告警）；测试端点 IM 行滤未绑定；`_REASON_TOKENS` 补 `bad_target`。

---

## 一、public API（稳定，可跨模块调用）

### notify.py（单例 `AlertNotify.get()`）
```python
AlertNotify.get() -> AlertNotify             # 单例（__init__ 连 Valkey）
.notify(level: Level, title: str, body: str, channel: str | None = None) -> str
    # channel=None 按 level 路由（_route）；返回 alert_id（去重 key）
    # 1min 内同 title+level 合并（_is_deduped + _append_body）；超日配额跳过（_quota_exceeded）
    # 走 get_channel(target).send(title, body, level)；无渠道记 warning（不抛）
.report(title: str, body: str, channel: str = "wechat_work") -> None
    # 盘后报告分发（info 级，完整内容）；等价 notify("info", ...)
Level = Literal["info", "warn", "critical"]
```

### channel.py（PT1 MessageChannel 抽象，详见接口契约 §3）
```python
MessageChannel(ABC): .send(title, body, level="info") -> bool / .test() -> bool
get_channel(provider: str) -> MessageChannel | None
    # 从 channel_config 读 credentials_encrypted + decrypt + 实例化；无配置/失败返回 None
_REGISTRY: dict[str, type[MessageChannel]]   # wechat_work / discord / serverchan
```
- **实现**：`WechatWorkChannel`/`DiscordChannel`（继承 `_WebhookChannel`，webhook_url）/ `ServerChanChannel`（sckey）
- **level 前缀**：title 加 `[level]` 前缀（如 `[critical] 磁盘告警`）

---

## 二、内部 API（不保证稳定，改模块时才能动）

- `AlertNotify._route(level) -> str`：按级别选渠道（当前都走 `wechat_work`；critical 可加 discord）
- `AlertNotify._dedup_key(title, level) -> str`：`md5(f"{title}:{level}")[:12]`（alert_id）
- `AlertNotify._is_deduped(key) -> bool`：Valkey `alert:dedup:{key}` setex 60s；存在=已去重
- `AlertNotify._append_body(key, body)`：`alert:body:{key}` append `\n---\n{body}`（合并同标题）
- `AlertNotify._quota_exceeded(channel) -> bool`：`alert:quota:{channel}:{YYYYMMDD}` incr；超 `ALERT_DAILY_QUOTA`（默认 100）跳过
- `AlertNotify._record(alert_id, level, title, body, channel)`：写 Valkey `alert:{alert_id}` hash + `alert:history` lpush/ltrim 999
- `_WebhookChannel._post(payload) -> bool`：httpx.post + status==200；异常返回 False（不抛）

---

## 三、依赖（import 其他模块什么）

| 本文件 | 依赖 | 用途 |
|---|---|---|
| notify.py | redis（外部）/ dotenv | Valkey 去重/配额/历史 + .env |
| channel.py | `data_platform.db.get_conn` | 读 channel_config（get_channel 内） |
| channel.py | `web_api.crypto_utils.decrypt` | 解密 credentials_encrypted（get_channel 内） |
| channel.py | httpx（外部） | webhook POST |

> ⚠️ `alert_notify.channel` 依赖 `web_api.crypto_utils`（解密），`web_api` 反过来依赖 `alert_notify`（get_channel 测试端点）。**循环依赖**，靠函数内 import 延迟打破。改签名注意。

---

## 四、被谁调用（改 public API 签名要同步改这些）

| 调用方 | 调什么 |
|---|---|
| `scheduler.tasks` | `AlertNotify.get().notify`（health_check/drift_check/reconcile/disk_monitor/broker_health）/ `.report`（daily_report） |
| `web_api.main` | `get_channel`（消息通道测试 + 发送）/ `_REGISTRY`（通道类型枚举）/ `AlertNotify`（TODO 核实 report 端点） |
| `task_manager.notify_on_failure` | `get_channel(provider)` + `.send`（PT7 跨层联动，任务失败告警） |

> 改 `notify`/`report`/`get_channel` 签名影响**全项目告警出口 + 任务失败通知**——慎改。

---

## 五、读写表

| 表 | 写 | 读 |
|---|---|---|
| `channel_config` | web_api（消息通道端点） | `channel.get_channel`（provider/credentials_encrypted/enabled） |

> 本模块**不写任何 PG 表**；告警历史/去重/配额全在 Valkey（见下）。

**Valkey 键**：
- `alert:dedup:{key}`（setex 60s，去重标记）
- `alert:body:{key}`（合并 body 累积）
- `alert:quota:{channel}:{YYYYMMDD}`（incr + expire 86400，日配额计数）
- `alert:{alert_id}`（hash：level/title/body[:500]/channel/ts，单条记录）
- `alert:history`（lpush + ltrim 999，最近 1000 条 alert_id 列表，供 Web 展示）

---

## 六、不变量

- **level 三档**：`info` / `warn` / `critical`（title 前缀 `[level]`）
- **去重**：1min 内同 `title+level` 合并 body（`_is_deduped` + `_append_body`），不重复发
- **配额**：每渠道每天默认 100 条（`ALERT_DAILY_QUOTA` env 可配），超限跳过 + warning
- **不抛**：渠道发送失败 / 无可用渠道 / 解密失败 → 记 warning 返回 None/False，不阻断调用方
- **body 截断**：`_record` 存 `body[:500]`（Valkey hash 体积控制）
- **PI1 迁移**：渠道从 .env 迁到 `channel_config` DB（2026-08-08），`get_channel` 是唯一入口
- **历史保留**：`alert:history` 最近 1000 条（ltrim 0 999）

---

## 七、扩展指南

### 加新渠道（如钉钉/邮件）
1. 实现 `MessageChannel` 子类（`send(title, body, level)` + `test()`）
2. `channel._REGISTRY["dingtalk"] = DingtalkChannel`
3. Web 配 `channel_config`（provider='dingtalk'，credentials 加密，enabled）
4. 不改 `notify.py`（`get_channel("dingtalk")` 自动工作）

### 加分级路由（如 critical 同时发 discord）
- 改 `_route(level)`：critical 返回多渠道分发逻辑（当前只返回单渠道字符串，需扩展为 list 或循环 send）

### 调配额
- 改 `.env ALERT_DAILY_QUOTA=200`（不需改代码）

---

## 修订记录
- 2026-08-11 初版（基于代码核实：notify.py:1-108 / channel.py:1-102 / __init__.py 全读）

- 新增 safe_notify（never-raise 包装，收编三处重复 try/except notify 模式）
