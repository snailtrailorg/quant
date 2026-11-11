# D32 复审 delta（审官 B2 · 可实施性/迁移视角）

- 对象：`docs/design/D32-接入插件架构.md`（446 行，a2468c5；HEAD 5c2250f 未改本文）
- 方式：只读；对照 §/代码逐条核 delta，不重审已闭环项
- **结论：仍须改。** 5 条 P0 全部实质闭环，但新引入 2 处契约级缺口（注册表域词表、双租约退出语义）+ 2 处旧账未销（S7 client_id、数据侧 legacy 删除批次），4 项改完即可立项。

## ① 闭环

| 原项 | 新稿位置 | 判定 |
|---|---|---|
| P0-1 注册表 | §十:366-374 复合键 `(domain,provider)` + 8 处降只读投影 + 等值守门；§一.2:24 逐点列名；`binance`/`binance_perp` 双键已明说 | 收编口径成立（细节缺口见 ③1） |
| P0-2 事件推送 | §六:142-144 `subscribe_order/subscribe_trade` + §六:179 事件语义；§十二:399-400 P1a 签名 / P1b 接线 | 可实施 |
| P0-3 角色化 | §9.3:331 `build(account: AccountDTO, role, slot)`；§3.1:57-62 凭证解密/必填校验 md_only/连接窗/号段全划出插件；AccountDTO 不读库不解密（:338） | 与 Q7 清单逐项一致 |
| P0-4 号段 | §9.5:351 SETNX+TTL、EX_TEMPFAIL(75)、禁公式回落、`reserve_md`；§十二:410 部分唯一索引 | 可实施（`account_key` nullable/`enabled` 列已核 0116:105/114/123） |
| P0-5 P1 拆分 | §十二:399-400 P1a/P1b + §十四:428 KNOWN_VNPY_IMPORTS 棘轮 + :439 突变复测 | 矛盾解除 |
| Q3 双轨 | §十二:409-413 impl 列 + account_key 部分唯一 + (account,role) 租约 + 回滚=整 release | 齐（租约冲突见 ③2） |
| Q5 S4 | §5.1:123-126 单笔冻结+CFG_VERSION_KEY+TTL 兜底；§7.5:265-274 四条退役 | 主体闭环 |
| Q8 paper / md_session | §十三:420 paper=内置插件；§3.1:60 + §9.5:353 md_session 归编排层 | 闭环 |

## ② 未闭环

1. **S7 与租约冲突（阻断 P3 验收）**：§十四:434 仍断言「client_id 序列」同输入同结果，但 §9.5:351 已改租约分配——旧路径 `(tid-1)%98+2` 与新分配器必然不等。全文无 legacy-compat 模式（Q4-3 未采纳）→ §十二:402 「S7 全绿」不可达。
2. **S7 可测性残项**：查询稳定窗（:434）未写时钟注入/窗参数化；`adapters.py:370-448` 的 `time.sleep(2)` 直睡会 flaky。
3. **legacy 删除批次不全**：§十二 P3-P5 只覆盖 xtp/emt/crypto；数据侧 4 处（`data_source._REGISTRY`、`adapters/base._ADAPTERS`、`interfaces/base._PROVIDER_MODULES`、markets 四表）在 P1 投影后**无删除批次**（P6 是研究），A03 是否接管未写。
4. **配置 schema 双源退役未写**：§6.6:193 + §9.1:305 给了去处，但 `FIELD_SCHEMA/PARAMS_SCHEMA`（`interfaces/base.py:40-41,90`，消费 `mgmt.py`）在 P1-P5 期间与 `manifest.config_schema` 并存，表单/必填校验分叉无退役批次。
5. **`md_api_guard` 未点名**：仅靠 §十二:402「MD+TD 同包」间接带走；它 import vnpy_xtp 内部 `PROTOCOL_VT2XTP/XtpMdApi`（`md_api_guard.py:32-33`），P3 文件清单漏列即整半 S1 红。
6. **S1 扫描面窄**：§十四:428 只列 `__import__`，未列 `importlib.import_module`（`adapters.py:477`）；PEP562 未写「value 传递可达」判据。现 `test_layering.py` 非递归盲区也未提。
7. **版本不可读回落未写**：§7.4:258 读失败=用旧快照，与 Q5「退回逐次 DB 读」不同；§5.1:126 只说 TTL 兜底，未说版本键读不到怎么办。
8. **SA4 期望源**：§十一:383 只写 market 过滤；`list_md_gateway_providers()`（`tasks.py:1877`）仍驱动 hub 期望，插件化后该白名单的同步/退役未安排。

## ③ 新引入

1. **复合键无域词表（阻断 P1a）**：§十:368 键=`(domain,provider)`，但 §9.1:298-308 manifest 无 `domains` 字段；§十:374 `PORT_CAPS` 是端口词表、§十:369 举例却用「数据源/交易通道」——两套域词。且 §5.1:110 `REGISTRY.get(account_row.provider, role)`、:119 `REGISTRY.caps(provider, impl)` 与复合键签名不符（`impl`∈{legacy,plugin} 更不是域）。P1a 无法按现文实现。
2. **两套租约、两种退出码**：§9.5:351 slot 租约失败=`EX_TEMPFAIL(75)`；§十二:411 per-(account,role) 互斥租约失败=`EX_CONFIG(78)`。同元组两条租约、两种语义（瞬态 vs 永久），若同一键则冲突，若两键则重复。
3. **「连接窗内移」自相矛盾**：§十二:402 写 P3「连接窗内移」，§3.1:60/§9.5:353 写窗判定留编排层、插件只执行。实施者会二选一。
4. **开放问题与正文冲突**：§十五.3:445 仍把 SessionSlotAllocator 载体（Valkey vs DB）列为待定，§9.5:351 已定 Valkey SETNX。
5. **`build` 少 need**：§5.1:111 断言 `plugin.capabilities ⊇ need`，§9.3:331 `build(account, role, slot)` 无 need，manifest 也无 `capabilities` 属性（只有 data_capabilities/port_caps）。
6. **唯一索引维度不足**：§十二:410 `(account_key) WHERE account_key IS NOT NULL AND enabled` 无 market 维度；跨市场同名资金账号（如同一券商两市场同名账号）会被误阻，现有 `(provider,account_key)` 唯一（0116:123）不受此约束。

## 结论

**仍须改**——仅 4 项：③1（manifest 补 `domains` 并统一域词 + 改 §5.1 取键签名）、③2（两租约合并或明确分工与退出码）、②1（S7 增 legacy-compat 号段模式，否则 client_id 等价不可断言）、②3（补数据侧 4 处 legacy 删除批次）。其余 ①②③ 项可在 P1a/P1b/P2 任务文件内消化。改完即可进入 P1 立项。
