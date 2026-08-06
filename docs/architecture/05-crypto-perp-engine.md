# 05 - 加密永续合约引擎

## 1. 目的

币安 / OKX 永续合约（BTC/ETH 主流大币种）的全自动 CTA / 网格 / 对冲交易。底层 vnpy 加密网关（REST + WebSocket，Linux 原生无障碍），本模块负责策略与配置层，并叠加加密专属风控。

## 2. 职责

1. **策略实例管理**：4H/1H 趋势 CTA、区间网格、波动率突破、对冲。
2. **配置驱动**：Web 端配置，复用 02 schema，`type=crypto_perp`。
3. **执行接入**：`BinancePerpAdapter` / `OKXPerpAdapter` 翻译统一 Order → vnpy 加密网关请求。
4. **杠杆/仓位模式**：固定低杠杆上限、逐仓隔离。
5. **24h 运行**：加密无时段限制，7×24h 常驻。
6. **资金费率/爆仓监控**：取资金费率因子，爆仓预警。

## 3. 边界与非目标

- **底层 vnpy 加密网关是第三方**，本模块只做策略/适配/配置/专属风控层。
- **不做**：小币种/山寨币（只 BTC/ETH 主流）；现货（只永续合约）；跨交易所搬砖套利。
- **非目标**：高频做市（延迟要求高，个人平台不做）。

## 4. 依赖

- 策略框架（02）：Strategy、Factor、`BinancePerpAdapter`/`OKXPerpAdapter`
- vnpy 加密网关（第三方，币安/OKX）
- 数据中台（06）：K 线历史、实时 WS 行情、资金费率
- 风控中心（07）：加密专属风控（杠杆上限/逐仓/插针防护/单日亏损熔断）
- 告警（10）：爆仓预警/单日熔断/异常推送（加密 24h，需即时推送）

## 5. 接口

### 5.1 适配器
```python
class BinancePerpAdapter(ExecutionAdapter):
    def send_order(self, order: Order) -> str:
        req = self._to_vnpy(order)        # 含杠杆/逐仓/止盈止损
        return self.gateway.send_order(req)
    def set_leverage(self, symbol: str, leverage: int): ...
    def get_funding_rate(self, symbol: str) -> float: ...
class OKXPerpAdapter(ExecutionAdapter): ...   # 同形
```

### 5.2 策略类型
```python
@register_strategy("crypto_cta_trend", adapter="binance_perp", timeframe="4H")
class CryptoCTATrend: ...            # 4H/1H 趋势
@register_strategy("crypto_grid", adapter="binance_perp")
class CryptoGrid: ...                # 区间网格
@register_strategy("crypto_volbreak", adapter="binance_perp")
class CryptoVolBreak: ...            # 波动率突破
```

### 5.3 加密专属因子（02 注册制，category=crypto）
- `funding_rate`（资金费率，套利/趋势辅助）
- `volatility_break`（波动率突破）
- `trend_4h`（4H 趋势）
- `basis`（基差）

## 6. 数据流

```
币安/OKX WS ─> 数据中台(实时K线+资金费率) ─> 策略.on_bar/on_tick ─> 因子 ─> 信号
  └> 风控.check_order(含加密专属) ─> BinancePerpAdapter.send_order ─> vnpy网关 ─> 交易所
  <─ 成交/爆仓回报 ─> 持仓更新 ─> Valkey+PG ─> Web实盘看板
异常行情(插针/暴跌) ─> 风控自动撤单+暂停策略 ─> 告警即时推送
```

## 7. 加密专属风控（与 07 联动）

| 项 | 默认 |
|---|---|
| 杠杆上限 | 固定低杠杆（如 ≤5x），策略不可超 |
| 仓位模式 | 逐仓隔离（单合约爆仓不影响其它）|
| 插针防护 | 异常瞬时价格波动 → 自动撤单 + 暂停策略 |
| **断线策略冻结** | WS 断线重连期间（如 10s）旧单可能成交、新行情缺失，策略基于过时数据开仓会仓位混乱。网关 disconnected 超 3s → **暂停开新仓**（只允许平/撤）；重连后**先 query_position 再恢复 on_bar**，用实盘持仓对账而非内存状态 |
| 爆仓预警 | 维持保证金率逼近阈值 → 即时告警 |
| 单日亏损熔断 | 单日亏损超阈值 → 仅平仓不开新仓 |
| 资金费率监控 | 极端资金费率 → 提示/调整 |

## 8. 配置 schema（复用 02，type=crypto_perp）

```json
{
  "id": "btc-cta-4h",
  "type": "crypto_perp",
  "symbol": "BTCUSDT-PERP",
  "adapter": "binance_perp",
  "exchange": "binance",
  "factors": [{"name":"trend_4h","weight":0.6},{"name":"funding_rate","weight":0.4}],
  "aggregator": {"method":"weighted_sum","threshold_buy":0.2,"threshold_sell":-0.2},
  "risk": {"leverage_max":5,"margin_mode":"isolated","daily_loss_limit":0.05},
  "params": {"timeframe":"4H"},
  "enabled": true
}
```

## 9. 与其它模块交互

- **风控中心**：加密专属规则在 `check_order` 里叠加；杠杆/逐仓在 adapter 层强制；`emergency_halt` 全停。
- **数据中台**：24h 实时 WS，历史 K 线回测。
- **告警**：24h 需即时推送（微信/Discord），尤其爆仓/熔断。
- **Web 后台**：启停、改参数、看持仓盈亏/爆仓风险。
- **回测**：VeighNa，加密无时段，可全历史回测。

## 10. 关键设计决策

| 决策 | 选择 | 理由 |
|---|---|---|
| 标的范围 | 只 BTC/ETH 主流永续 | 个人平台，控风险 |
| 杠杆 | 固定低上限 + 逐仓 | 杜绝高杠杆风险，爆仓隔离 |
| 执行底层 | vnpy 加密网关 | 成熟，REST+WS，Linux 无障碍 |
| 策略层 | 复用 02 统一基类 | 与场内引擎一致 |
| 插针防护 | 风控自动撤单+暂停 | 加密极端行情常见 |
| 24h 运行 | systemd 常驻 + 告警即时 | 无时段限制 |
