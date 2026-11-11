# 批 172a 只读盲审 A（契约面）· general-purpose-1

① 通过项
- Order(6)/Position(7)/Trade/OrderState/AccountState 字段名与 `adapters.py:27-45` 逐字一致；`Position.available` 是派生 `@property`（trade.py:36-39）✓
- Bar 11 字段与 `contract.py:240-243` 同序同义 ✓；Tick 平铺字段与 `md_gateway.py:8-18` 契约等价（open_price/high_price/low_price 对齐 vnpy TickData，文档里 open/high/low 是缩写）✓
- ports 方法名逐条对齐 D32 §六（Execution 12 / MarketData 11），无缺失无多用 ✓；registry 三域词、`(domain,provider)` 复合键、PORT_CAPS 五词、get→KeyError、pick→UnsupportedCapability 与 §十 一致 ✓；LAYER 三个新条目本身未引入实际上行边 ✓

② P0
1. `domain/enums.py:35-43` OrderStatus 取值与现有代码不逐字一致，P1b「零转换」不成立：`PART_TRADED="part_traded"`，真实回写是 `partial`（`trading.py:82`，`scheduler/tasks.py:414` 亦按 'partial' 查）；缺 `submitted`（`strategy.py:519`）、`send_failed`（`trading.py:394`）、`interrupted`（`trading.py:412`）；`not_traded` 全仓无对应（`trading.py:76` 明确 NOTTRADED 不回写）。D32 §12.2 要求「取值与现有代码逐字一致」→ 须裁决为 order_log 词表全集，或显式声明该枚举只表交易所回报并配映射表。
2. S1 守门对 `vnpy_xtp` 全盲（§0.1 硬约束点名的对象）：`tests/test_d32_s1_vnpy_boundary.py:28-29` 只认 `vnpy`/`vnpy.*`（实测 `_is_vnpy("vnpy_xtp")=False`），基线因此漏 `md_api_guard.py:33`、`md_gateway.py:202`、`td_registry.py:60` 三处 `from vnpy_xtp...`。后果：P3 删这三行时「只减不增」账不平，且 quant_common/ports 里新增 `import vnpy_xtp` 全绿。该批突变自检只测 `import vnpy→__import__` 形态，覆盖不到此洞。须把顶层包前缀扩为 vnpy|vnpy_xtp|vnpy_binance|vnpy_okx 并补基线三行。

③ P1
3. `ports/history.py:15` `fetch(req: Any) -> Any` 与 D32 §六（DataRequest→ContractFrame）不符，且注释理由不成立：两类型都在 `quant_common/contract.py:170/253`（同为层 0、纯 stdlib），收紧注解零耦合成本。同族 `ports/account.py:16` `entitlements()->Any`（§六 为 AccountSpec）。
4. registry 与产出契约三处不符：无公开 `REGISTRY`（`:29` 只有 `_REGISTRY`，任务契约第 158 行与 §十 均以 REGISTRY 命名）；**legacy 只读投影完全缺失**（任务产出 3 与 §12.1 P1a 出口条件「唯一表 + legacy 只读投影」）——注意层 1 无法投影层 2/3 的旧注册点，须先改 D32 落位或撤该出口项；`pick(:75)` 给 `impl` 默认值，与冻结签名 `pick(domain,provider,need,impl)` 不等价。
5. `register(:51-65)` 不校验 `manifest.id/domain` 与实参一致（§十 启动校验「id 唯一」），同实例重复注册静默通过，与自身 docstring「重复注册同一键视为错误」矛盾。
6. 分层守门对新目录实际失效：`tests/test_layering.py:62-80` 相对 import 解析 off-by-one——实测 `src/ports/execution.py` 的 `from ..web_api import x` 解析结果为空（被当自包丢弃），`data_platform` 内部相对 import 被记成 `('data_platform','db')` 一类 47 条假边。ports/registry/providers 只用相对 import，故 LAYER 新增三行对它们零约束。须修 base_idx 并造一条 ports→web_api 突变复测。另 `quant_common` 第三方白名单（`:112-124`）用 `os.listdir`+`endswith(.py)`，不覆盖 `domain/` 子包，「纯数据零第三方」无守门。

④ P2（不阻塞）
- `test_d32_s3:113-117`「删包即失效」断言的是 `clear()` 测试钩子，不是包移除语义。
- `Position.available` 加 `max(...,0)` 夹紧，现有 `SUM(volume-frozen)` 不夹紧（`strategy.py:361`）；§12.2「派生关系以现有代码为准」下属新增语义。
- 任务文件「产出」列 8 个 domain 文件（position/tick/bar/health），实现合并为 5 个（其接口契约段本身按 3 文件分组），须同步。
- Order/Position `frozen=True` vs adapters 可变 dataclass：P1b 换源前确认无原地改写消费者。

⑤ 结论：**须改**（P0×2 + P1×4 未闭合；均为签名冻结前的低成本修正，后两项需先回 D32 改文档再编码）。
