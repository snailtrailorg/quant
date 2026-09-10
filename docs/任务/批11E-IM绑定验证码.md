# 批11E · IM 绑定验证码自动确认 v2

> v2 = 方案双盲审 A（P0×1 P1×4 P2×7）/B（P0×1 P1×3 P2×6）修订全吸收。
> 核心修正：独立键**存码**（原稿存 owner=匹配器无物可比，A/B 同判 P0）；成功即消费 GETDEL；条件插入防 TOCTOU 双绑。

## 1. 流程（v2）

```
qr 向导 done 且 ins 真新建（B-P1-1/A-P2：仅 ins 且 owned:True——重扫/平台级不发码）
  → codegen=secrets 六位数字（A-P2）
  → 双写同 try（B-P2）：①session done 载荷 bind_code ②Valkey im:bindcode:{bid} = code（TTL 900，键值=码；
    owner 从 im_bot_config 现读校验（A-P1-1：防转归属后旧键绑错人））
用户私聊发码给机器人（receive_id_type=='open_id' 才进码分支——A-P1-4 群聊隔离）
  → 文本归一化（B-P1-2：NFKC 全角转半角+剥 @_user_N mention 前缀+去全部空白）
  → == 键中码 且 GETDEL 原子消费（A-P0-1/B-P2：先到者独得）
  → owner=DB 现读；条件插入（INSERT...SELECT WHERE NOT EXISTS 已绑行）防并发双绑
  → bind_owner + 回复「✅ 绑定成功」+ audit（owner_im_bind_autocode，不含码）
  → 码不匹配：错次 INCR，≥5 DEL 键（A-P1-3）；照旧拒答回显（留痕噪音随上限消解）
```

## 2. 安全边界（v2）

- 键模板+codegen+redis 工厂+match_consume **单点 im_bot/bindcode.py**（B-P1-3：VALKEY_URL 同 tasks 构造，禁两处漂移）
- 码禁入 logger/audit detail（A-P1-2）；ws_client 既有全量 data print=所有聊天内容本就进 journal 的既有面——接受并挂账（消息脱敏另批）
- admin 面 onboarding-status **过滤 bind_code 字段**（B-P2）；自助轮询他人 404（既有）
- webhook fid=None 防呆不触发（B-P2）；TTL 全 900 对齐（A-P2）
- 群聊（chat_id 型）不进码分支；绑定成功后 DEL 键（手动绑成功同 DEL）

## 3. 范围

| # | 改动 | 文件 |
|---|---|---|
| 1 | 新模块：`_key()`/`gen_code()`（secrets）/`issue(bid, code)`/`match_consume(bid, text, chat_is_p2p)`（归一化+GETDEL+错次上限） | `im_bot/bindcode.py` |
| 2 | tasks ins 分支发码双写 | `feishu_bot/tasks.py` |
| 3 | 拒答分支：p2p 先试 match_consume→成功回复+audit；失败走原拒答 | `im_bot/feishu_client.py` |
| 4 | admin status 响应 pop bind_code | `im_bots.py` |
| 5 | Profile done 显示验证码大字+「私聊发给机器人」引导（bindCodeHint，含"无响应稍候重试"） | `Profile.vue`+locales |

## 4. 不做（明示）

待绑定手动列表保留兜底；**跨 bot 已绑用户给第二个 bot 发码不可达**（resolve 已解析不进拒答分支——pending 常驻，A-P2：手动通道覆盖）；form 建 bot 不发码；ws_client 消息 journal 脱敏（既有面挂账）

## 5. 测试

码生成仅 ins+owned:True（重扫/平台级不发）/归一化（全角/@前缀/空格）/匹配自动绑（GETDEL 单次+条件插入）/码错不绑+错 5 次 DEL/已绑不绑/fid=None 不触发/admin status 无码/p2p 限定/转归属后旧码绑 DB 现值
