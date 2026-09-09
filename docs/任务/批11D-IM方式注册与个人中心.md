# 批11D · IM 通道方式注册（模型修正）+ 个人中心改版 v2

> v2 = 方案双盲审 A（P1×3 P2×7）/B（P1×2 P2×6，实测路由行为）修订全吸收 + 用户通道清单（飞书✅/钉钉/企微/微信个人号）。
> 两层模型（通道×方式）：通道注册声明方式集合，UI 据注册能力自动生成，同平台多方式并存。

## 0. 通道清单（用户 2026-09-10 裁定）

| 通道 | 状态 | 方式画像（模型预演） |
|---|---|---|
| 飞书 | ✅ 已实现 | `qr`+`form` |
| 钉钉 | 下批候选 | `form`（ClientId+Secret）+`qr?`（Stream Mode——dingtalk-stream SDK 是否病友型先十分钟实测再定 qr） |
| 企业微信 | 下批候选 | `form`（CorpID/AgentId/Secret/Token/AESKey）+`post_steps`（回调 URL 创建后到企微后台配——模型本批加槽） |
| ~~微信个人号~~ | ❌ 用户裁定出局（2026-09-10：无官方 API，风险不接受） | — |

## 1. 范围

| # | 改动 | 文件 |
|---|---|---|
| 1 | **模型**：`ONBOARDING_METHODS: dict[str, dict]`（键=方式标识；值 `{kind: manual\|interactive, label_key, fields?(manual), wizard?(interactive), post_steps?[]}`）；飞书声明 `{"qr": {kind:interactive, wizard:"feishu_register", label_key:"imBots.method.qr"}, "form": {kind:manual, fields:FIELD_SCHEMA, label_key:"imBots.method.form"}}`。**`ONBOARDING` 旧单值改 `@property` 派生=`any(kind=="interactive")`（顺序无关，A-P1-3/B-P1-2）**；`list_providers()` 返回加 `methods`（fields 与顶层 field_schema 同源本体）。方法注册表映射：`_WIZARD_TASKS = {"feishu_register": <task>}` 硬编码白名单（不做反射，A-P2-3） | `im_bot/base.py`+`feishu.py` |
| 2 | **重扫路径 owner 判定**（A-P1-1+B-P1-1 合并修）：task 加 `owner_user_id=None` 参数；重扫分支——`row.owner 非 NULL 且 ≠ task owner` → session **error**（"该应用已被他人接入"）；`row.owner NULL 且 task owner 非 NULL` → 只合并凭证**不翻 enabled 不改名**，done 带 `owned:false`（前端提示归属）；admin 面（owner=None）维持现状（翻 enabled+改名）；INSERT 冲突后重查行同走此判定（A-P2-6）。**task 落库分支补 audit_log**（bot 是 task 建的，B-P2-5④） | `feishu_bot/tasks.py` |
| 3 | **自助向导端点**（插 my- 组头部 providers 后、全部 `{bid}` 路由前——真撞型=`GET onboarding-status/{ticket}`(五段) vs `{bid}/pending`(五段)，B-P2-1 纠偏）：POST `/api/my/im-bots/onboarding/{provider}/{method}`（注册表校验：provider 幽灵 404/method 幽灵或 kind=manual **400**（method_not_interactive）/wizard 白名单查 task→delay 带 owner；**每用户同时 1 个活 ticket**——起前扫 Valkey session 归属该用户且非终态即 429（A-P1-2）；**每用户 bot 配额 ≤5**（数 owner 行）400；audit `owner_im_onboarding_start`）；GET `/api/my/im-bots/onboarding-status/{ticket}`（**session 载荷比对 sub 不符 404**，A-P2-2） | `im_bots.py` |
| 4 | **Profile 三 tab**（TabsShell：基本信息/IM 通道/修改密码）：基本信息=头像+昵称+只读行+**注销段置底**（B-P2-4①）；修改密码=现表单迁入；IM 通道=现列表段迁入 | `Profile.vue` |
| 5 | **添加弹窗按 methods 生成**：provider 下拉**隐藏 methods 空的**（A-P2-4）；单方式直进/多方式 label 选择；`kind=manual`→FIELD_SCHEMA 表单；`kind=interactive`→扫码区（起任务→`qr_img` base64 展示→10s 轮询→done 刷新；**轮询三件套**：弹窗 close/watch 停+onUnmounted 清 timer+**600s 前端兜底超时**（后端 pending 混同过期永不报，B-P2-4②③）；done 且 `owned:false`→提示归属他人/平台；label 兜底链 `te(label_key)?t(label_key):t(\'imBots.methodKind.\'+kind)`（A-P2-5）） | `Profile.vue` |
| 6 | `feishu_register_task(session_id, owner_user_id=None)` 签名向后兼容（默认值——celery 新 web→旧 worker 秒级窗口 TypeError 失败自愈，B-P2-2 已知会） | `tasks.py` |
| 7 | locales zh/en（method 标签/methodKind 兜底/tab 名/扫码流程文案/错误码 ONBOARDING_BUSY/BOT_QUOTA/METHOD_NOT_INTERACTIVE） | `locales/index.js` |

## 2. 关键设计（v2 增补加粗）

- 两层模型+**派生 any()（顺序无关）**+**wizard 白名单映射**（admin 面硬编码 provider=="feishu" 一版内并存，admin 改版时统一收口 wizard 驱动——B-P2-6④）
- **重扫三分支**（他人=error/平台=只刷凭证+owned:false/自己或 admin=现状）
- **频控+配额**：每用户 1 活 ticket+5 bot（A-P1-2 收口 11C 挂账的量级依据）
- ticket 归属绑定（session 载荷 owner）；**qr 建即 enabled vs form 建即停用=有意差异**（写操作指导）
- celery 同 release 秒级签名窗口自愈

## 3. 不做

钉钉/企业微信实装（下批候选；钉钉接前先实测 dingtalk-stream SDK 病友性）；post_steps 槽只定义不消费（企微批实装）；admin 面 ImBots.vue 改版；wizard 状态机扩展

## 4. 验证（v2 补）

单测：methods 结构+**onboarding 派生=any() 钉值**（正反序都 interactive）/幽灵 provider 404（兼路由序钉子——被 {bid} 吃=422 即败）/幽灵 method 400/**合法 manual method（form）走 onboarding 端点 400**/频控（活 ticket 429）/配额（6th bot 400）/ticket 归属（他人 404）/重扫三分支（他人 error/平台 owned:false 不翻 enabled/自己现状）/task INSERT 带 owner+audit/INSERT 冲突重查
本地端到端：三 tab/方式切换/form 建停用 bot/qr mock 轮询三态+600s 兜底（真扫码留用户手机实测）
smoke：Profile 三 tab 断言（新增，不依赖用户数据）
部署：纯代码（无迁移，owner 列 0072 已在）→ staging → prod
