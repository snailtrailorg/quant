# 批 172b · 方案 v4.1 delta 复核（代理 D）

> 只读复核，未跑测试。对照 B（P0-1/P1-1..4）、A-delta（P1-a/P1-b）、v4.1 任务文件、S1 守门、基线、实际代码。

1. 已闭环 — B P0-1：产出 4 构造含 `event_order/event_trade`（main 传 vnpy 常量），产出 13 删 trading.py:81/128/296/780+hub_worker.py:455 且 `# count` 47→42（实测基线恰 47 行：`tests/fixtures/vnpy_import_baseline.txt:5`），§C「零新 vnpy 依赖…`getattr(x,"name",None)` 字符串映射」写死；main.py:355 EVENT_ORDER 本就在基线，注入零新增。
2. 已闭环 — B P1-1：§D「**不整对象 translate**——`status_of(od)` 判工作态 + `getattr(od,"vt_orderid","")`」，工作态集={SUBMITTED,NOT_TRADED,PARTIAL} 与 `trading.py:296-303` 三段等价；产出 11 明确 `_order()` 保留两字段（`tests/test_trading.py:257-258`）。残余差异见 P2-2。
3. 已闭环 — B P1-3（契约面）：§C「main 用可变 holder…**cb 也从 holder 取 adapter**（禁止闭包未绑定局部量）」＋「adapter=None 时写库函数不得抛 AttributeError」＋D 段 cid 三分支（adapter 非空且有 `_vt2cid`→当前值／None→`d.client_id`），覆盖 `trading.py:98/139` 两处 `adapter._lock`。缺验收用例→P2-4。
4. 已闭环 — B P1-4：产出 14 `tests/test_d32_s8_role_isolation.py`（AST 双角色扫＋allowlist＋「扫描目标非空」自检）＋验收突变复测；D32:526/566 的 S8 本已挂在 P1b 出口。
5. 已闭环 — A P1-a：§C `guard_factory: Callable[[str], Callable[[Callable], Callable]]`＋`self._guard(name)(fn)`＋「main 传 `_guard`；hub_worker 传 `lambda n: make_guard(n, _alert)`」，与实测 `main.py:299-305`（收 name 返 deco）逐字吻合。
   已闭环 — A P1-b：`server/src/ports/execution.py:41` 与 D32:161、D32:493（§12.2）三处均已 `AccountState | None`，无剩余打架。
6. 无新 P0/P1。新发现 4 条 P2（均"写清一句/补一条用例"级，不阻塞编码）：
   - P2-1 `reconcile_orders` 未规定 `status_of` 单条隔离：raise 会顶到 `trading.py:775` 的整段 try，**整轮对账含成交补录中止**；基线（`:780-782`）只是把该单过滤掉。建议照撤单路径写死「单条 try/continue」。
   - P2-2 E#6「缺 vt_orderid→跳过并计 untranslatable」偏离 B 修法（基线会 `cancel_order("")`→异常→failed）：已在 E 表记档，可接受，但等价用例需按新语义钉，勿照基线断言 failed。
   - P2-3 双守卫未写死：hub_worker `on_trade` 已 `@make_guard("hub.on_trade")`（`hub_worker.py:445`），端口再包 main 的 `_guard` → 告警码由 `hub.on_trade` 变 `runtime.guard`、告警可能翻倍；建议契约写明或记入 E 表。
   - P2-4 验收缺「adapter=None 早到事件」用例（B P1-3 要求）；现有验收 5 只盖「缺字段事件 wrapper 不抛」。
   - 附：B 的 P1-2 前提有误——实测 `Direction.LONG.value == 'Long'`（venv `vnpy/trader/constant.py:14` `_("多")` 经 locale 翻成 'Long'），B 建议的 `{LONG:"多"}` 映射表反而制造回归；v4.1 E#3「用原始 `.value`＋当前环境实测 Long/Short/Net」正确，勿采纳 B 的映射表。

VERDICT: PASS
COUNTS: P0=0 P1=0 P2=4
