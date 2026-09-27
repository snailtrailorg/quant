# 批 68 · order_log 终态回写（批 61 挂账——在途判定根治）

> v1（2026-09-27 勘察+方案）。来源：批 61「明确不做（挂账）：order_log 终态回写（在途根治）」。

## 一、现状与根因

- **order_log.status 只写提交侧**：`_update_order_status`（strategy.py:505）流转 submitting→submitted/send_failed 后**永停**——成交/撤单回报只写 trade_log（write_trade_log），order_log 终态无人回写。
- **三处消费方复合判定绕行**（status IN ('submitted','submitting') + NOT EXISTS trade_log）：①trade_switch._OPEN_SQL（批 61 在途两源之一）②tasks.py:392「委托不成交」告警 ③同族对账。复合判定=在途判定被「trade_log 有无」间接代理——部分成交单（trade_log 有行+order 仍开放）被误判非在途；僵尸 submitted 单永存。
- EVENT_ORDER 已被 adapter._on_order（adapters.py:117）收进缓存但无终态消费方；order_log.vt_orderid 列 F-50 已备（重启反查键）。

## 二、方案

### 回写链（对齐 on_trade 模式——4a 单源化惯例）
- `trading.py` 新函数 `write_order_status(d, adapter, sid, symbol)`：EVENT_ORDER 回调消费——vnpy Status 映射后回写。
- 注册点：`strategy_runner/main.py _run_hub_mode` 的 ee.register 链（on_trade/on_log 同位）加 `on_order`（guard 包裹同款）。

### 状态映射（值域立法）
| vnpy Status | order_log.status | 说明 |
|---|---|---|
| SUBMITTING/NOTTRADED | （不回写） | 提交侧已管/已报=非推进 |
| PARTTRADED | `partial` | 中间态（可继续推进） |
| ALLTRADED | `all_traded` | 终态 |
| CANCELLED | `cancelled` | 终态（含部撤） |
| REJECTED | `rejected` | 终态 |

**终态集** = ('all_traded','cancelled','rejected')；**只进不退**守卫：Python 判序（提交态→partial→终态单调）后单条 UPDATE（WHERE status NOT IN 终态集）。

### 关联键
- 主键=vt_orderid 直查（F-50 列）；空则 `_vt2cid` 反查 client_order_id（write_trade_log 同款双路）；都无→warning 跳过（不炸主流程）。
- 幂等：重复回报同状态=UPDATE 同值无害；回写 never-raise（guard+try）。

### 消费方不动（观察期注记）
三处复合判定**本批不改**——回写上线后新单终态真实，存量僵尸 submitted 单靠 _OPEN_SQL 当日窗自然出窗；「委托不成交」复合判定保留至观察期（周一窗）后另批简化（届时 status 终态判定替代 NOT EXISTS）。

## 三、限定范围
不动：三处消费方 SQL（观察期后另批）/adapter._on_order 缓存（保持）/EmtAdapter 形状合成（批 63 已对齐 vnpy Status ✓）/Web 端状态展示（另行）。

## 四、验收
1. pytest 全绿（+钉：映射四态/只进不退〔终态后 partial 迟到不退〕/vt 空反查/never-raise）。
2. staging：无真单窗——回写链 mock 钉+**周一观察窗真单验证**（XTP/EMT 回报驱动 order_log.status 终态落地——如实声明本批 staging 行为级空窗）。
3. pyflakes 零新增。

## 五、mock 方式
fake OrderData（vnpy 形状：vt_orderid/status 枚举）；mock get_conn 断言 UPDATE 参数；guard 注册钉（ee.register 调用含 EVENT_ORDER）。
