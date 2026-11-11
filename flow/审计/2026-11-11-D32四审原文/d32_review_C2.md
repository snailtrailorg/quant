# D32 复审（C2 · 只审 delta）· 只读

> 对象：`docs/design/D32-接入插件架构.md`（446 行，a2468c5；工作区另有 1 行 P5 路径修正）
> 基线：原 C 审 `/tmp/d32_review_C.md`（5 P0 / 11 P1 / 8 P2）

## ① 闭环（原审项已修，逐条取证）

| 原项 | 判定 | 证据 |
|---|---|---|
| P0-2 规格双真源 | **闭** | :197「不建 market_spec/instrument_spec」；§7.1/7.2 逐字段映射 `market_hours`/`security_master`/`band_rules`；§7.5 旧源退役清单 |
| P0-3 research token | **闭** | :280 明写 research 进 `PORT_CAPS` 不进 `CAPABILITIES`，并引值域断言；S10(:437) 钉同向；与 `test_markets_registry.py:19-20` 不冲突，无需改守门 |
| P0-4 前件两表 | **闭** | :70 前件更正 0116/0122；正文其余引用均 `trading_account`(:100/:383/:409) 或 `data_source`；`external_interface` 仅存于更正句 |
| P0-5 §6.6 映射 | **闭** | :185 Broker 不并入端口（→credentials+config_schema）；:187 DataSource→A03 PullCapability 且明写不丢 ref_data/inst_event；:163 HistoryPort 降为 A03 门面 |
| P1 关键项 | **闭** | credentials() 已删(:171-174)；HistoryPort 改 `DataRequest→ContractFrame`(:162-164)；pick 加 DB `enabled∧capabilities`(:371)；manifest.priority 删除(:372)；REGISTRY 复合键(:368-369)；ExecutionAdapter「语义对应、签名不等价」(:186) |
| P2 导航 | **部分闭** | `design/README.md:34` 已改「插件细节见 D32」；`architecture/README.md:10` 已含 D27-D31/D33——两项**无需再动** |

## ② 未闭环

1. **P0-1 仍开口（唯一 P0）**：§3.1「单一归属表」(:55-68) **没有「provider 注册/能力声明」这一行**——正是 P0-1 的标的；§十 :368「唯一声明处」未做 A03 carve-out；而 :108 `role: md|td|pull`、:331 `Literal["md","td","pull"]` 仍把拉取插件放进 D32 组合器，与 :49「数据供给归 A03」相反。实施者按 :108 落码即在 D32 重建第 9 处注册表。
2. **P1-2 尾部**：:187 只承接了 A03 的限速/熔断/用量，**未写明 D31 窗口/地板/水位归 A03、插件只做无状态区间抓取**。
3. **连接窗措辞自撞**：:402 P3 验收写「连接窗**内移**」，与 §3.1:60「窗判定归编排层、插件只执行 connect/suspend」歧义；按字面会把窗判定搬进插件。

## ③ 新引入（本轮改写产生）

1. **:226 品类枚举写错**：声称「以 `ASSET_CATEGORIES` 为基集（stock/**etf**/fund/...）」，实际 `quant_common/markets.py:47` 是 `listed_fund`。违反正名裁定，且照抄会与 S5 枚举对不上。
2. **:301 xtp `data_capabilities: [rt_quote]` 漏 `trading`**：代码/测试 `provider_capabilities("xtp") == {"trading","rt_quote"}`（`test_markets_registry.py:26`）。若 manifest 成为 :311 所述「写侧 ⊆ 校验」的真源，现有 XTP 账号行 `{trading,rt_quote}` 会被判越集拒写。
3. **:111/:110 契约名不存在**：`plugin.capabilities` 未在 §9.1/§9.3 定义（只有 `port_caps`/`data_capabilities`）；`REGISTRY.get(provider, role)` 与 :368 键序 `(domain, provider_id)` 相反。
4. **:400 `providers/*/translate`** 未出现在 §9.2 包结构（只有 `_transport.py`），P1b 无落点。
5. **:443 开放问题 1 已被 :224 回答**（symbol 覆盖优先级已定），留着会诱导二次设计。
6. **:60「读 `md_session`」** 措辞失准：`md_session` 是模块不是表，窗配置真源为 `system_config` 键（`load_xtp_window_cfg`）。

## 四、D32 之外连带修改清单（行号已 grep 实证）

**必须改（不做则 D32 前件/交接即断）**
1. `docs/design/D26-市场接入架构.md:49` — 「凭证/连接/**号段/连接窗**/重连全在插件内」与 D32 §3.1 相反 → 改「凭证/连接/重连在插件；号段分配与连接窗判定在编排层（D32 §3.1）」。
2. `docs/design/D26-市场接入架构.md:56` — `trading 域 external_interface` → `trading_account`（0116/0122 后态）。
3. `docs/architecture/接口契约.md:18` — DataSource 的 DB 表 `external_interface` → `data_source`；`:47` Broker 的 DB 表 → `trading_account`。
4. `docs/architecture/接口契约.md:294` — 加一句「D32 目标态：ExecutionPort（P3 增设适配层）；P1a 期以本节现行签名为准」。
5. `docs/architecture/A02-外部接口与市场维度设计.md:11-13,23,34` — `external_interface`→两表；四旧 token `daily/minute/snapshot/quote`→5 token；:23 品类 `etf`→`listed_fund`。
6. `docs/architecture/A05-多账号源架构设计.md:34,63,84` — `external_interface`→`trading_account`（:84 FK 改 `REFERENCES trading_account(id)`）；`:58` `etf`→`listed_fund`。
7. `docs/architecture/A01-总体设计.md:21` — 「执行规则下沉到 ExecutionAdapter」→「差异=provider 插件+声明式规格（见 D32）」，与 D32 §一.4/§7.3 对齐。
8. `flow/待办.md:21` — 删「需 A02/D25 扩第 6 token」→「`research` 为端口能力（PORT_CAPS），**不进** CAPABILITIES，无第 6 token」（D32:280 已定）。:20 批次行已写 P1a/P1b，**无需改**。
9. `docs/architecture/A03-数据供给总线设计.md:40,336,356`（:687 同）— `external_interface`→`data_source`/`trading_account`；D32 把 pull 归 A03，A03 指已删表则交接断裂。
10. `docs/architecture/模块契约/data_platform.md:65,76,340` — 仍写 `external_interface` 读写；代码 `routing.py:205` 已读 `data_source` → 改两表名。

**可延后（P3 迁移或历史批注，不阻塞 D32 立项）**
11. `docs/architecture/模块契约/adapters.md:82-86` — 加「D32 目标态：迁 providers/；P3 前本节为现行基线」。
12. `docs/architecture/模块契约/md_hub.md:23,72` — `create_md_gateway` 加「D32 P3 由 REGISTRY 取代」注记。
13. `docs/architecture/接口契约.md:461,531,532,565` — 历史批注中的 `external_interface` 加「旧表名（0116 前）」括注。
14. `docs/design/D25-三层能力模型.md:20,30,83` — :20 的「5 token=daily/...」是 v1 陈述，加「v2 起见 :58」；:30/:83 旧表名加历史括注。
15. `docs/design/D26-市场接入架构.md:159` — 迁移历史行，加「（旧表名）」注记即可。

## 结论

**须改后再审**（范围已收窄：P0 仅剩 §3.1/§十 的注册与能力声明归属，P1 两处措辞），另 ③ 的 1/2 两项是事实级错误须同改；其余原 P0/P1 已实测闭环。连带清单第 1、8 项与 D32 正文直接矛盾，须与 D32 同批修，否则两真源。
