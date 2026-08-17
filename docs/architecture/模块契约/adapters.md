# 模块契约 · adapters（策略执行适配器）

> 本模块的 public API + 依赖 + 被调 + 读写表 + 不变量。任务改本模块前读本文件，不用读整个项目。
> 配套：`docs/architecture/接口契约.md`（§执行层 Order/Position/ExecutionAdapter）。本文件不重复数据结构定义，只列"本模块暴露什么"。

## 职责
**策略执行层**：把 `Signal`（BUY/SELL/HOLD）翻译成具体通道的下单/撤单/查询。
- `ExecutionAdapter`（ABC）+ 2 个实盘实现（`XTPAdapter` 场内 / `CryptoPerpAdapter` 加密）+ 工厂 `create_adapter`
- > `BacktestAdapter`（回测，按 bar 收盘价成交）是 `ExecutionAdapter` 的第三个实现，但**文件在 `backtest.py` 不在本文件**（见被调说明）

## 文件结构
```
server/src/strategy_framework/adapters.py   # ExecutionAdapter ABC + XTPAdapter + CryptoPerpAdapter + create_adapter + Order/Position
```
> `BacktestAdapter` 在 `server/src/strategy_framework/backtest.py`（同包，不在本文件），通过 `strategy_framework/__init__.py` 一并导出。

---

## 一、public API（稳定，可跨模块调用）

> `Order` / `Position` / `ExecutionAdapter` 签名详见接口契约 §执行层。以下只补实现行为。

### adapters.py
```python
@dataclass Order:    # symbol/action(BUY|SELL)/volume=0/price=0.0/order_type="limit"|"market"/client_id=""
@dataclass Position: # symbol/volume/avg_price/pnl=0.0

class ExecutionAdapter(ABC):
    send_order(order: Order) -> str        # 抽象，返回 order_id（client_id）
    cancel_order(order_id: str) -> None    # 抽象
    query_position() -> list[Position]     # 抽象
    # 以下默认空，子类按需 override：
    query_account() -> list               # XTPAdapter 实现
    query_orders() -> list                # 事件缓存读
    query_trades() -> list                # 事件缓存读

class XTPAdapter(ExecutionAdapter):
    def __init__(self, gateway=None, event_engine=None)
        # gateway = vnpy_xtp.XtpGateway；event_engine 缺省取 gateway.event_engine
        # __init__ 注册 4 事件监听（EVENT_ORDER/TRADE/POSITION/ACCOUNT）-> 缓存 dict
    @staticmethod parse_vt_symbol(vt_symbol) -> tuple[str, str]   # "603986.SHSE" -> ("603986","SHSE")
    send_order(order) -> str              # client_id（自增 c1/c2...）；维护 cid<->vt_orderid 映射
    cancel_order(order_id) -> None        # order_id 按 cid 映射查 vt_orderid + CancelRequest
    query_position() -> list[Position]    # 调 gateway.query_position() 后 _wait_update 轮询等事件（2s）
    query_account() -> list               # 调 gateway.query_account() 后 _wait_update
    query_orders() -> list                # 纯事件缓存（XTP 无主动查委托）
    query_trades() -> list                # 纯事件缓存

class CryptoPerpAdapter(ExecutionAdapter):
    def __init__(self, gateway=None)
    set_leverage(leverage: int)           # 上限 5x（max(1, min(leverage,5))）
    # send_order/cancel_order/query_position 占位（gateway=None 返回 mock-crypto-*）

create_adapter(adapter_type: str, gateway=None, event_engine=None) -> ExecutionAdapter
    # adapter_type: "xtp" -> XTPAdapter(gateway, event_engine)
    #               "binance_perp"/"okx_perp" -> CryptoPerpAdapter(gateway)
    # 未知抛 ValueError
```

---

## 二、内部 API（不保证稳定，改模块时才能动）

- `_vnpy_exchange(ex: str)`：项目后缀（SHSE/SSE/SZSE）-> vnpy `Exchange` 枚举（延迟 import；缺省 `Exchange.SSE`）
- `XTPAdapter._on_order/_on_trade/_on_position/_on_account(event)`：事件回调，`event.data` 入缓存 dict
- `XTPAdapter._cid2vt` / `_vt2cid`：client_id <-> vt_orderid 双向映射
- `XTPAdapter._cid_seq`：client_id 自增计数器
- `XTPAdapter._wait_update(cache, before, timeout=2.0)`：轮询等缓存 key 变化（vnpy 异步查询同步化）
- `CryptoPerpAdapter._leverage` / `_margin_mode`：杠杆 + 逐仓（默认 isolated）

---

## 三、依赖（import 其他模块什么）

| 本文件 | 依赖 | 用途 |
|---|---|---|
| adapters.py | vnpy.trader.constant（Direction/Offset/OrderType/Exchange） | send_order/cancel_order 内 **lazy import** |
| adapters.py | vnpy.trader.object（OrderRequest/CancelRequest） | send_order/cancel_order 内 lazy |
| adapters.py | vnpy.event（Event）/ vnpy.trader.event（EVENT_*） | __init__ 注册监听 lazy |

> 全部 vnpy import 在函数体内（延迟），顶层无 vnpy 依赖 → 无 vnpy 环境也能 import 本模块（如纯回测/单测）。⚠️ 实盘三级开关**不在本模块**（`risk_control.check_order` 前置 + scheduler 层检查）。

---

## 四、被谁调用（改 public API 签名要同步改这些）

| 调用方 | 调什么 |
|---|---|
| `strategy_framework/__init__.py` | 重导出 `ExecutionAdapter`/`XTPAdapter`/`CryptoPerpAdapter`/`create_adapter`/`Order`/`Position` |
| `strategy_framework.strategy.py` | `from .adapters import Order`（`place_order` 构造订单） |
| `strategy_framework.backtest.py` | `from .adapters import ExecutionAdapter, Order, Position` + 定义 `BacktestAdapter(ExecutionAdapter)` |
| `strategy_runner/main.py`（实盘进程） | `from src.strategy_framework.adapters import XTPAdapter`（`XTPAdapter(gateway, event_engine)`） |
| `strategies/convertible_doublelow.py` | `create_adapter`（顶部 import，回测内当前未用） |

> 改 `ExecutionAdapter` ABC 方法签名 → 影响所有子类（XTPAdapter/CryptoPerpAdapter/BacktestAdapter）+ 所有调用方。改 `create_adapter` mapping key → 影响 `strategy_config.adapter` 字段取值。

---

## 五、读写表

| 表 | 写 | 读 |
|---|---|---|
| — | — | — |

> 纯执行层，不读写 DB。凭证由 `Broker`（`broker.py`，PI3 后 `XTPAdapter.__init__` 用 `get_broker("xtp").get_credentials()`）提供；风控由 `risk_control` 前置。

---

## 六、不变量

- **Order.action**：`"BUY"` / `"SELL"`（大写字符串，非枚举；`XTPAdapter.send_order` 用 `.upper()` 容错）
- **order_type**：`"limit"` / `"market"`（缺省 limit；market -> vnpy OrderType.MARKET）
- **send_order 返回值 = client_id**（非 vnpy vt_orderid）：XTPAdapter 自增 `c1`/`c2`...；`BacktestAdapter` 返回 `bt-N`；gateway=None 返回 `mock-*`
- **cid <-> vt_orderid 映射**：`XTPAdapter` 维护双向 dict，`cancel_order(client_id)` 经映射查 vt_orderid
- **gateway=None 安全**：所有 send_order 返回 mock 字符串不报错（便于无实盘环境测试）
- **vnpy 查询异步**：query_position/account 调 gateway 触发后 `_wait_update` 轮询 2s 等事件推送；query_orders/trades 纯被动事件缓存（XTP 无主动查委托/成交接口）
- **CryptoPerpAdapter 杠杆上限**：`set_leverage` 钳到 [1,5]
- **三级开关不在本模块**：下单前由 `Strategy.place_order` -> `risk_control.check_order` 前置（实盘开关 AND）

---

## 七、扩展指南

### 加新交易通道（如 CTP/IB 股票）
1. 本文件加 `<X>Adapter(ExecutionAdapter)` 子类（实现 send_order/cancel_order/query_position；查询类按需 override）
2. `create_adapter` mapping 加一行（如 `"ctp": CTPAdapter`）
3. `strategy_framework/__init__.py` `__all__` 加导出
4. `strategy_config.adapter` 字段用新 key（Web 配置即生效，不改调用方）

### 接入实盘凭证（PI3 模式）
- `XTPAdapter.__init__` 内改用 `get_broker("xtp").get_credentials()` 取凭证（替代 .env 直读）
- Broker 抽象在 `broker.py`（配置 + 连接测试），与 ExecutionAdapter（交易执行）分工

---

## 修订记录
- 2026-08-10 初版（基于代码核实：adapters.py 全读 + backtest.py BacktestAdapter + 被调 grep）

> ⚠️ 2026-08-17 语义变更（WAL 时序/order_prefix/fail-closed/verify 证据门禁等）：见 `docs/architecture/接口契约.md` 末节「今日语义变更」。
