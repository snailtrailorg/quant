# D32 复审（红队 delta）· a2468c5 / 446 行

审法不变：假设实施一年后出事，倒推失败路径；每条落到新稿节 + 真码。只审 delta，不重提已闭合论述。

## ① 闭环（原 5 条 P0）
- **F1 表述**：§0.2-3 + §9.6:356-364 + §十三:419 已把"热插拔"降级为"随 release 交付、盘中禁升级"，per-plugin `requirements.txt` 已删（§9.2:324 单 lock）。承诺与 A06 一致——**但 §9.6 的验收 S3 守不住（N3）**。
- **F4-双源方向**：§7.5:265-274 有退役表，`band_rules` 定为涨跌停唯一真源、`price_band` 只引用（与 §7.2:215 一致），概念自洽；**但迁移映射本身不可实施（②）**。
- **F6**：§5.1:104-126 拆 `get_runtime`（冷）/`resolve_rules`（热）+ 单笔冻结 + 版本号失效，闭环。
- **F7/F8 词表**：§8:280 + §10:374 分离 `CAPABILITIES`/`PORT_CAPS`，S10 断言；S1 改基线棘轮。方向闭环，机制有洞（N5）。
- **F5 方向**：§十二:409-413 用 `impl` 列 + `account_key` 部分唯一 + 租约 + 整 release 回滚，比原稿"REGISTRY 保证唯一"诚实；**但租约覆盖面与作用域不成立（②/F5、N1）**。

## ② 未闭环
- **F2 签名仍缺 tid，且新增死锁**：§9.3:331 `build(account, role, slot)` 有 role 无 tid；§9.5:351-352 说前缀 `t{tid}:e{epoch}:` "经 slot 传入"，可 slot 由 `acquire(account_id, role)` 分配、键 `slot:{account_id}:{provider}:{role}`——**tid 无处来、slot 每账号唯一**。今天 tid 是 live_task 维（0025:5"一标的一进程"；`(tid-1)%100+20` td_registry.py:143/165），同账号多任务多 TD 会话是现状且 D26:68 专门做同账号 client_id 占用校验。
- **F2 后果被 §十二 放大**：per-(account,role) 互斥租约（:411）⇒ 同账号只许一个 TD runtime，第二个标的的任务拿不到 slot → EX_TEMPFAIL 恒重启。**"撞号解决"是拿"一账号只能跑一个任务"换的。**
- **F3 三分表有空格**：§7.4:258"读失败→用旧快照"未定义首单/重启后无快照（缓存本就是懒加载，§5.1:126）；平仓遇 PG 抖动且无快照 → 表里无格可落。
- **F3 验收不达意**：S9 只断言"平仓必放"（不抛错），不断言挂得出去。缺规则时 `tick_size` 未知，`round_close_price` 回落 0.001（hub_quote.py:81），股票需 0.01（0092:47）→ 价非法被拒单，S9 仍绿。
- **F4 退役映射维度错位**：`close_policy.*` 键是 provider×category（hub_quote.py:132-140），5 键里 `has_limit_band/aggression/opponent_depth/max_order_qty` 是**执行策略**不是标的属性；§7.5:269"迁移为 security_master 列"= 把品种/柜台策略拍扁到逐 symbol 行，几千行回填、provider 级回落链消失，且未给列名/缺省/优先级替代 → 实施必自造，双源变三源。
- **F4 优先级无存储**：§7.2:224 写 `(exchange,board,category) > (category)`，可 `security_master` 只有 symbol 行、`band_rules` 只有 exchange×board → 板块/品类级缺省只能活在代码，"运营改不发版"对品类级规则不成立。
- **F5 租约覆盖面**：§十二 未要求 legacy 路径也持租约 ⇒ 手工 `systemctl start` 旧单元仍双开；且租约跑本机 Valkey（server/.env:8 `127.0.0.1`），而 §十一:385 允许"两机共库"——**跨机互斥不存在（N1）**。

## ③ 重写新引入（按严重度）
- **N1 P0 · 租约作用域 vs 两机共库**：§十一:385 允许同库多机，§十二:411 的唯一性靠 Valkey 租约，可 Valkey 是 per-host（127.0.0.1）。两机同市场（扩容/过滤配错/手工起旧单元）各自拿租约成功 → 两 TD 会话、两个 order_prefix → 双下单。需共享 Valkey 或 DB 行锁（§十五-3 仍在"倾向 Valkey"）。
- **N2 P1 · 分片没管订阅面**：`_desired_symbols` 查 `live_task WHERE status='running'` **无 market/account 过滤**（md_hub/main.py:269），D26:65 明写"各 hub 全量复制订阅"。分片后 astock 机 hub 仍订阅其他市场的 running 标的（XTP 白订阅/报错），两机同市场则 L2 授权费×N 再翻倍。§十一 只写"进程过滤（SA4）"，全篇没碰 `_desired_symbols`/`hub_transient_subs`。
- **N3 P1 · S3 证不了 §9.6**：S3 的假插件用现成市场+现成依赖，只能证"加插件不坏旧插件"。真新增市场要动 `MARKETS`（quant_common）；新 SDK 要动全局 lock（§9.2:324）→ 与"核心目录零改动"直接冲突；`git diff --stat` 也无法把 `providers/__init__.py` 的一行注册与任意核心改动区分（除非加 allowlist＝放水）。
- **N4 P1 · DEPLOY_MARKETS 写错层**：systemd 单元随 release 树发布（rollback-tasks.yml:92 实证），跨机同 artifact、同内容 ⇒ 不能在单元里装 per-host `Environment=`（也不该在 §十一 与 inventory 二选一并列）。真做须新增 release 外的 `/etc/quant/deploy.toml` + 装位 + systemd 读入，属 A06 炸半径（新输入/白名单/双写），文中一句"machine file"掩盖了这条改造。
- **N5 P1 · 注册表三套签名**：§5.1:110 `REGISTRY.get(provider, role)`、§十:368 键 `(domain, provider_id)`、§10:371 `pick(domain, provider, need, impl)`；而 manifest（§9.1:299-308）**无 domain 字段**，xtp 同时占 market_data+execution 两域 ⇒ "一行注册"与"两把键"打架，实施者必挑一个然后把另外两处留成死文。
- **N6 P1 · `role: pull` 双拉取链**：§9.1 roles 与 §9.3 含 `pull`，但 §6.6:187 明确 DataSource→A03 PullCapability。D32 插件再 build 一个 pull runtime = 第二条拉取链，pacing/breaker/usage 三件套落哪边无定义。
- **N7 P1 · S1 file:line 棘轮会自失效**：基线按 `(file:line)` 钉，任何上方插行整体位移 → 批量假红 → 惯例"重生基线"把同批新 import 一起洗白（守门自失效族，记忆已有两例）。应按 `(file, module, symbol)` 多重集钉。
- **N8 P2 · 租约续租失败未定义**：§9.5 只写"拿不到→75"，没写续租失败；md_hub 有 `lease-lost` 即退先例（main.py:361），D32 未继承。续租失败但连接仍活 → 旧进程继续交易，新进程若取到不同 terminal_id = 双会话双下单（取同值则被 XTP 撞回，纯运气）。
- **N9 P2 · `register_dynamic_rule(category, fn)` 键不足**：§7.3:246 只按 category 注册，而表格同节把"券商口径差异"列为动态机制（provider 维）→ 两 provider 同品类规则互相覆盖、注册顺序即行为。
- **N10 P2 · account_key 索引建不起来**：现库唯一键是 `(provider, account_key)`（0116:123）、`account_key` 可空（0116:109）。§十二:410 的全表部分唯一索引在存量跨 provider 重复/NULL 行上会建失败或形同虚设（NULL 行仍可双开）。

## 结论（二值）
**仍须改。** 五条 P0 里 F1/F6/F7/F8 已实质闭合，F3 从"打平仓"降到"验收不达意"，但 **F2 是契约级未闭合且有新死锁**（build 无 tid + slot 每账号唯一 → 同账号只能一个任务），**F4 退役映射维度错位**、**F5/N1 的互斥作用域跨机不成立**。前两项在写 P1 代码前改文档即可（补 `slot` 携带 `{tid, boot_epoch}` 并改成 per-(account,role,tid) 或 per-client_id 槽；§7.5 给 5 键逐列映射 + provider 级回落替代）；N1/N2/N4 属 §十一 新章，须与 D26 订阅面、A06 输入面一起改，否则分片一落地就是"机器级配置 + 本机租约 + 全局订阅"三处不一致。
