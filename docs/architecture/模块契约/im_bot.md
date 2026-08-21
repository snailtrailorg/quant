# im_bot 模块契约

> 层 3 服务层(19 号 IM 统一接入,2026-08-21 批 2 上线)。完整 IM 接入面抽象--区别于 alert_notify 的 MessageChannel(单向告警出站)。
> 新平台=实现 IMBotProvider 子类+locales 加字段词条+DB 配一行,平台代码零改动。

## 一、public API

### base.py(抽象+注册表)
```python
IMBotProvider(ABC)
    .FIELD_SCHEMA: list[dict]      # 凭证字段声明(单一真相源):[{key,type(text|secret|select|number|boolean|textarea),label_key,secret?,options?}]
    .required_fields: set[str]     # FIELD_SCHEMA secret 字段推导
    .MODE: str                     # webhook | websocket | long_poll | hybrid(飞书=hybrid:消息 ws+卡片 webhook)
    .ONBOARDING: str               # manual | interactive(扫码/回跳辅助接入)
    .connect(bot_id, on_message)/.shutdown(bot_id)   # 长连接生命周期(默认 no-op,webhook 型无需)
    .send_text(bot_id, receive_id, receive_id_type, text) -> bool
    .send_card(bot_id, receive_id, receive_id_type, card) -> bool
    .verify_callback(bot_id, headers, body) -> ("challenge",{})|("message",d)|("card",d)|None
    .build_confirm(tool, args, reason) -> card / .parse_confirm(action_value) -> dict|None
    .test_connection(bot_id) -> (bool, str)
    .start_onboarding() / .poll_onboarding(ticket)   # interactive 型

get_im_provider(provider) -> IMBotProvider | None    # 注册表取(lazy 触发 feishu 注册)
list_providers() -> list[dict]                       # 前端下拉+动态表单数据源(GET /api/im-bots/providers)
```

### feishu.py(FeishuProvider) / feishu_client.py(自 feishu_bot/bot.py 下沉)
```python
FeishuClient(bot_id=None)      # 凭证读 im_bot_config(bot_id=None=最新 enabled 行)
get_feishu_client(bot_id)      # per-bot 单例(TTL 300s;凭证热更新最多 5 分钟,即时生效走 stop/start 重启)
evict_feishu_client(bot_id)    # 凭证写路径后主动失效(同进程)
process_message_async(open_id, text, receive_id_type, receive_id, fid)   # 消息->LLM->回复(3s 约束)
execute_confirmed_tool(open_id, tool_name, args)                        # 确认卡片执行(熔断/恢复/策略启停)
check_user(open_id) -> role|None          # im_bot_users 表->env 兜底(fail-closed)
verify_event_signature / verify_card_signature(ts, nonce, body, sig)    # 官方算法;密钥主源 im_bot_config,env 兜底;卡片 fail-closed
```

### credentials.py / users.py
```python
get_bot_credentials(bot_id) -> dict          # 解密 JSON;无/失败返回 {}
save_bot_credentials(bot_id, creds, partial=True) -> bool   # 整 JSON 重加密+route_key 同步
list_users/upsert_user/delete_user(bot_id, ...)             # im_bot_users CRUD
```

## 二、依赖与被调
- 依赖:data_platform(db)/quant_common(crypto)/llm_gateway/risk_control/feishu_bot 无依赖(B-S3 分层修正:bot.py 下沉后 feishu_bot 是 re-export 薄壳)
- 被调:web_api(/api/im-bots 14 端点群)、feishu_bot(router webhook+ws_client 长连接入口)、scheduler(feishu_register_task 扫码)

## 三、读写表
| 表 | 写 | 读 |
|---|---|---|
| im_bot_config | credentials/save/扫码/web CRUD | FeishuClient/签名/授权/web 列表 |
| im_bot_users | upsert_user/delete_user/导入脚本 | check_user/web users 端点 |

## 四、不变量
- 凭证 JSON 整串加密存 credentials_encrypted;有任一非空字段才存(全空=NULL)
- params.route_key 与 credentials 的 id 字段同写(唯一索引 (provider, route_key) 防漂移)
- 卡片确认链 fail-closed:密钥表+env 皆空即拒
- 授权决策链:im_bot_users -> default_role(19 号批 3 补) -> 拒
- 分层:test_layering 断言 im_bot(层 3)零上行边

## 五、迁移记录
- 0051:建两表+feishu_config 全列数据迁移(密文容错解密+失败告警)
- 0052:DROP feishu_config(批 2 切完全部读路径后)
