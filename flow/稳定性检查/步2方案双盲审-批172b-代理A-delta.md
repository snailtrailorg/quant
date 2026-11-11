# 批 172b 方案 v3 · delta 复核（代理 A）

**结论：带 P1 通过**（P0 已清零；新引入 2 × P1、6 × P2）

> 复核范围：对 `flow/任务/批172b-D32-P1b-事件接线.md`（v3）与 `docs/design/D32-接入插件架构.md`（未提交 diff）
> 逐条回对我上一轮的 2 P0 / 8 P1 / 7 P2。只读，未改代码。

## 一、原 2 P0 —— 已闭环

| 编号 | v3 处置 | 判定 |
|---|---|---|
| P0-1 验收与限定范围互斥 | 产出 11 明确列出 5 个测试文件 + 硬约束「只允许换输入形态/ctx 增键/断言换 domain 字段，禁止放宽或删除原断言」；验收改为「16 条全绿且断言逐条未放宽」 | **闭环**。`main.py:299-305` 的 `_guard` 与 `alerts.make_guard:57-59` 都是「返回装饰器」形状，改后的断言仍走同一 SQL 路径，条数可保 |
| P0-2 翻译层在守卫之外 | ACL 契约明确 `wrapper = guard_factory(name, fn)`，`fn = adapter_getter() → cb(translate(...))`；且 `adapter_getter` 要求永不抛（未绑定返 None）；现状表新增 #8 记录该立法 | **设计闭环**，但**落地依赖 P1-a**（守卫工厂签名写错则守卫根本装不上） |

## 二、原 8 P1 —— 逐条判定

| 编号 | 处置 | 判定 |
|---|---|---|
| P1-1 cancel 闭环只在同会话成立 | D32 §6/§12.2 改为「client_id best-effort；cancel 必须先解析为 vt，解析失败显式失败」；ACL 走 `get_vt_orderid` + query 缓存反查，失败 `raise LookupError` | **闭环（语义面）**。恢复场景仍**不具备**撤销能力，只是变响亮——见 P2-1 |
| P1-2 未知状态默认成在场态 | `status_of` 缺失/None/未知 → `raise ValueError`（fail-closed） | **闭环**（副作用见 P2-4） |
| P1-3 translate 失败被吞成 failed=0 | `_cancel_working_orders` 单条失败 `failed += 1` + warning + 继续、不增 requested | **闭环** |
| P1-4 trade_ref NULL→"" 撞唯一索引 | `trade_ref = d.tradeid or None`；验收 2「空 trade_ref 连续两笔 → 两行」 | **闭环** |
| P1-5 cid 命中无行不得回落 vt | 「cid 非空只查 cid，查不到**不回落** vt；cid 空才查 vt」+ 验收 3 | **闭环**（与 `write_order_status` 的相反顺序并存，见 P2-3） |
| P1-6 translate 归位 | 移到 `strategy_runner/legacy_translate.py`；D32 §9.2/§12.1 同步（P3 起 providers 才放 SDK 翻译，P3 同删 legacy_translate） | **闭环**，合同一处不留旧路径 |
| P1-7 非事件面零验收 | `query_positions/query_account/resolve_vt_order_id/connected` 委托/翻译，`connect/close` no-op；`to_domain_account` 0 行→None、多行 warning+取首；验收 6/7 补条目 | **闭环**（多账户聚合差异见 P2-2） |
| P1-8 模块契约未纳入 | 产出 12 + 「整篇自洽」 | **闭环** |

## 三、新引入问题

### P1-a `guard_factory` 签名与仓内两只守卫工厂**形状不符**，照契约接线必 `TypeError`（启动即炸）

契约原文：

    def __init__(self, event_engine, adapter_getter, guard_factory: Callable[[str, Callable], Callable], ...)
    # wrapper = guard_factory("legacy_port.on_order", fn)

仓内两个可用工厂都是「**只收 name，返回装饰器**」：

- `server/src/strategy_runner/main.py:299-305`：`def _guard(name): return _guard_base(name, alert=...)`（返回 deco）
- `server/src/strategy_framework/runtime/alerts.py:57-59`：`def make_guard(name, alert): return _guard_base(name, alert=...)`（第二参是 **alert 回调**，不是 handler）

⇒ `guard_factory("legacy_port.on_order", fn)` 在两个调用点都命中 `TypeError`（`_guard() takes 1 positional argument but 2 were given`；
`make_guard(name, fn)` 则把 handler 当 alert 回调，装饰期正常、异常时才在 alert 里再炸一次）。
两个调用点（main / hub_worker）都在**进程启动路径**上 ⇒ 实盘任务全线起不来。

**修**（二选一，写进契约即可）：
① 参数改 `guard_factory: Callable[[str], Callable[[Callable], Callable]]`，端口内 `self._guard(name)(fn)`，
   调用点传 `_guard` / `lambda n: make_guard(n, _alert)`；
② 保留两参形态，调用点必须显式适配 `lambda name, fn: _guard(name)(fn)`——但要在契约里写出这行，否则实施者按类型标注必然踩空。

### P1-b `query_account` 三处签名打架，v3 依赖的 None 与 D32 §6 冲突（且 §12.2 声明「端口签名=§六」）

- `server/src/ports/execution.py:31`（P1a 冻结产物）：`def query_account(self) -> AccountState | None`
- `docs/design/D32-接入插件架构.md` §六（现文）：`def query_account(self) -> AccountState`  ← **非可选**
- v3 契约 B：`to_domain_account(a) -> AccountState | None`，0 行→None；验收 7「query_account 空 list→None」

§12.2 又写「端口签名｜`ports/`｜§六 全部方法名与参数」。三处不一致 ⇒ P3 的插件作者按 §六 实现会得到永不返回 None 的端口，
而消费侧（v3 契约与验收）按 None 写判断；类型检查/静态审都会报。

**修**：D32 §6 一行改成 `-> AccountState | None`（与 P1a 冻结物一致），并在 §12.2 该行并入"含 Optional 语义"。

## 四、P2（残余与新引入）

1. **cancel 的恢复场景仍不可撤**：`_vt2cid`/`_cid2vt` 被 `EmtAdapter._check_eod` 清空（`adapters.py:949-953`）后，
   `cancel(cid)` → `get_vt_orderid`=None → query 缓存里所有 `client_id` 皆空 → 必 `LookupError`。v3 让失败变响亮（可接受），
   但验收 6 只写「解析成功撤 vt / 解析失败 raise」，**缺"映射为空"这条真实恢复用例**。建议补一条 = 把已知限制钉成断言。
2. **`query_account` 多行取首 vs `snapshot_cycle` 逐账户求和**：基线 `trading.py:227-240` 是
   `sum(balance)` 与 `sum(max(0, balance-frozen))`；v3 的 `available = balance - frozen`（**无钳位**）+ 多行取首。
   单账户下等价，多账户就不同。属"知情差异"，按基线 §5 第 3 条应记入任务文件（P3 切换 `snapshot_cycle` 时会被复用）。
3. **两个写函数的查询顺序相反，需并列写死**：`write_order_status` 是 vt 先、无行再按 cid（`trading.py:86-104`）；
   `write_trade_log` 是 cid 优先、互斥不回落（`:137-150`）。v3 只把后者写成"互斥"，前者靠"SQL 原样"隐含。
   建议在 D 段把两行并列写出，防实施者把"互斥"统一套到两个函数上（那会破 R2 的 cid 回退）。
4. **fail-closed 的副作用要记档**：基线对未知状态是 `m.get()`→None→**静默 return**；v3 变成 raise → guard 告警。
   这是**有意差异**，但矩阵只写了「缺失/None 状态 raise」，未写"该差异"本身；建议基线 §5 分类为「等价改写（可观测性增强）」，
   并让等价矩阵比对**告警码**（基线 §3 要求"告警/日志码"），不是只断言"有告警"。
5. **fixtures 覆盖不足**：产出 8 只列 `OrderData/TradeData`，但 `to_domain_position/to_domain_account` 也要测
   （P1-7 的验收 7 依赖它），需 `PositionData/AccountData` 构造器。
6. **验收 5「线程存活」在假 EventEngine 下不可测**：假 EE 只记录 handler、没有线程。该条应表述为
   「wrapper **不抛**（vnpy `_process` 无 try ⇒ 不抛即等价于线程存活）+ 告警被调用」，否则实施者会去断言一个测不到的量。

## 五、结论

- 2 个 P0：**处置正确**（P0-2 的落地正确性取决于 P1-a）。
- 8 个 P1：**全部闭环**，无一条靠"改断言/放宽标准"蒙混。
- 新增：P1-a 守卫工厂签名错（接线即 TypeError）、P1-b `query_account` 三处签名打架。
- 残余 P2 六条，均为"写清楚/补用例"级别，不阻塞编码。

VERDICT: PASS_WITH_P1
P0 = 0 / P1 = 2（均新增）/ P2 = 6
