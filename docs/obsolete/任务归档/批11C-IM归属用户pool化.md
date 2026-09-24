# 批11C · IM 通道归属用户（pool 化）v2

> v2 = 方案双盲审 A（P0×2 P1×6 P2×7）/B（P0×3 P1×5 P2×9）修订全吸收。两处关键事实修正：
> ①lark SDK `ws/client.py` 模块级全局 asyncio loop——**单进程跑不了第二个 ws.Client**（B-P0-1 实证）→ pool 改**子进程管理器**
> ②`process_message_async` fid 分支现状**直接用 default_role 不走 check_user**+首见登记把私聊者 upsert 放行（A-P0-1 实证）→ 身份源必须改绑定查询，否则"文档关洞代码开着"

## 0. 归属语义（v2 收紧）

- `im_bot_config.owner_user_id`=管理归属（谁建谁管）；**admin 面创建的 bot owner=NULL=平台级**（不写死 bot10——A-P2-6）
- `im_bot_users` 语义翻新：`im_user_id → user_id`（绑定**平台账号**）；**user_id 服务端钉死=认证会话用户，body 一律忽略**（A-P0-2）
- **身份源=绑定查询**（聊天+卡片全路径）：open_id → im_bot_users（user_id 非空行）→ join users（**无效账号 fail-closed**，A-P1-2）→ `load_effective_permissions(绑定用户)`；**user_id NULL 行（首见留痕）不获任何权限**（fail-closed 拒答+回一句"未绑定，open_id=ou_xxx 请在个人中心绑定"——同时解决 bootstrap 死锁 B-P1-2：用户从拒答消息里拿到自己 open_id）
- **role 列回落删除**（A-P0-1：回落=重建洞）；**env 兜底从聊天/卡片面摘除**（A-P1-3，告警 backfill 用途保留）；首见登记改**待绑定留痕**（不再授予权限，顺带止住告警收件人自然膨胀 A-P1-4——dispatch 收件人改 user_id 非空行）
- 自助创建/更新端点：**default_role/owner_user_id 服务端钉死**（default_role 恒 viewer 展示用；body 传入忽略）；(provider,app_id) 唯一性预检延伸自助（A-P1-5）
- **卡片确认面（card/callback）本批只对平台级 bot（owner NULL）开放**：URL 无 per-bot 路由，验签密钥"最新 enabled 行"在多 bot 下互踩（A-P1-1/P2-7）——自助 bot 的操作类卡片降级"请到 Web 执行"（fail-closed）；per-bot URL 路由挂下批
- gateway `_filter_tools` 四角色档位 → 补 perm 键档位（有 trade/halt 键=操作工具，B-P1-1/批11B 挂账提前消化）

## 1. 范围

| # | 改动 | 文件 |
|---|---|---|
| 1 | **迁移 0072**：im_bot_config+owner_user_id INT NULL；im_bot_users+user_id INT NULL（既有行 NULL=留痕待绑定）；seed：生产 feishu bot **全量归 admin**（role='admin' LIMIT 1，A/B-P2 合并） | `0072_im_owner.py` |
| 2 | **pool=子进程管理器**：`src/im_bot/pool.py` 主进程 30s 对账（**白名单键集 {id,enabled,credentials_encrypted}**，B-P1-5）→ spawn `python -m src.feishu_bot.ws_client {bid}`（现入口零改动，backfill 天然保留）/terminate 停用；坏 bot 子进程退避重试（60s 指数，封顶 10min）不传染（B-P0-2）；单元 `quant-im-pool@.service`（@quant 单实例，Restart=always 仅对 pool 主进程） | `im_bot/pool.py`+systemd |
| 3 | **身份链改造**：process_message_async fid 分支删 default_role 直用→绑定查询；check_user 改返回绑定用户（user_id 非空+users 有效+组权限）；首见登记改留痕（role='viewer' 占位防 CHECK 撞，B-P2-2）；卡片三道闸②改 perm 键+平台级 bot 门（router.py）；`get_feishu_client`/`_im_bot_secret` 卡片路径钉平台级 bot | `feishu_client.py`+`bot.py`+`router.py` |
| 4 | **自助端点**（require_authenticated）：GET/POST `/api/my/im-bots`+`/{bid}` 改删+`/{bid}/start|stop`（补齐 P2-A2——owner 守卫不放宽 admin 端点）；绑定管理：GET `/api/my/im-bots/{bid}/pending`（留痕 open_id 列表）+POST bind（user_id=会话钉死）；审计 `owner_*` 前缀+归因平台用户（A-P2-5） | `im_bots.py` |
| 5 | start/stop 语义=enabled 开关（pool 30s 生效；不再装拆单元） | `im_bots.py` |
| 6 | **Profile.vue 我的 IM**：列表卡+添加向导（FIELD_SCHEMA 抽 `ImBotForm.vue` 组件复用，B-P2-6）+待绑定 open_id 一键绑定（拒答消息引导闭环） | `Profile.vue`+`components/ImBotForm.vue`+locales |
| 7 | ImBots.vue 加 owner 列（admin 面） | `ImBots.vue` |
| 8 | **deploy 波次静态化**（B-P0-3）：inventory feishu 波 units → `[quant-im-pool@quant.service]`（deploy 可自助改 inventory；quant-dbro wrapper 是 root 特权不动）；**切换序列**（上产时执行）：装 pool 单元→起 pool→30s 拉齐 bot10→停+disable 旧 `quant-feishu-bot@10`（飞书多连接不互踢=过渡窗双进程行为等价可容忍）；**回滚 runbook**：翻链后 pool 单元 stop+disable→旧 @10 start（quant-svc 通道保留） | `deploy/inventory/group_vars/quant-prod.yml`+切换序列入本文件 |
| 9 | CLAUDE.md 19 号段回写 | `CLAUDE.md` |

## 2. 关键设计（v2 增补加粗）

- **子进程模型**：lark SDK 全局 loop 死结的唯一解（B-P0-1）；单 bot 隔离（坏凭证 bot 崩不连坐）；现 ws_client 入口/backfill 零改动；对账白名单键集防"改名即全平台断连"
- **权限同源**：聊天工具档+卡片执行面全部走 `load_effective_permissions`（与 Web require_perm 同键集同锁键）；锁键 {user_mgmt,resume,account_keys} 地板对 IM 面同样生效（resume=admin 专属钉子测试）
- **多绑定冲突 fail-closed**：同 open_id 多 bot 多行 user_id 不一致→拒（A-P2-1）
- webhook 并集路径同步换新链（user_id 非空行优先）

## 3. 不做（挂账明示）

- per-bot 卡片 URL 路由（本批平台级 bot 门替代）；多绑定邀请管理 UI；env LARK_AUTHORIZED_USERS 全退役（仅摘聊天/卡片面）；0072 后 role 列清理（下批随验证期过）

## 4. 验证（v2 补双盲审测试点）

单测：**fid 路径未绑定 DM 拒答+回显 open_id**（A 指最关键缺口）/绑定解析（user_id 优先/多义拒/停用账号拒/env 摘除）/自助创建 body 钉死（default_role/owner/user_id 不可控）/app_id 唯一性/(provider) 平台级卡片门/首见留痕不获权限/gateway perm 键档位/pool 对账逻辑（mock subprocess）/迁移 0072。
本地：pool 起假凭证 bot（子进程退避实证不传染）+Profile 全流程。
staging：pool 单元起+对账（staging 波次本不含 feishu——**波次改造生产首发即首验**，B-P0-3 提示风险已知会用户）。
smoke：Profile 我的 IM 段渲染。
