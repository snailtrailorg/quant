# 07 - 风控中心

> **平台化集成（2026-08-08）**：RiskRule 接口（PT6，src/risk_control/risk_rule.py + risk_rules 表），规则 DB 化，别人加规则实现接口。详见记忆 `platform-architecture`。

## 1. 目的

全局总风控 + 分市场独立风控的**双层防护**，是所有自动交易下单的强制前置关卡。一键熔断能力保证极端情况下能瞬时停所有自动开仓。

## 2. 职责

1. **前置校验**：每笔自动交易下单前必过 `check_order`，不通过则拒绝。
2. **全局风控**：总回撤、单日亏损、一键熔断。
3. **分市场独立风控**：场内（可转债/ETF）单标的仓位/日内次数/止损；加密专属（杠杆/逐仓/插针/爆仓/单日熔断）。
4. **运行态监控**：实时监控各账户回撤与亏损，触发阈值自动降级（仅平不开）。
5. **审计**：所有风控决策与触发写 `risk_log`，供 Web 风控面板展示。
6. **一键熔断/恢复**：Web 紧急按钮，停止所有自动开仓。

## 3. 边界与非目标

- **不替代策略内风控**：策略自身的止损/仓位管理仍在策略配置里，风控中心是**全局+分市场级的第二层兜底**，取严（策略配置与中心规则冲突时取更严）。
- **不校验 A 股下单**：A 股 adapter 永远 raise，到不了 `check_order`；但 A 股分析模块不涉及下单，自然不触发。
- **非目标**：不做实时对冲敞口精算（个人平台粗粒度足够）。

## 4. 依赖

- 数据中台（06）：账户持仓/盈亏/行情（算回撤）
- 策略框架（02）：`place_order` 调 `check_order`
- Web 后台（08）：一键熔断按钮、风控规则配置
- 告警（10）：风控触发即时推送
- Valkey：风控状态（熔断标志位）高频读写

## 5. 接口

```python
class RiskDecision:
    approved: bool
    reason: str
    severity: str        # info/warn/critical

class RiskControl:
    _instance = None
    @classmethod
    def get(cls) -> "RiskControl": ...

    def check_order(self, order: Order, account: str) -> RiskDecision:
        """所有自动交易 send_order 前必调。"""
    # 全局
    def emergency_halt(self, reason: str = "manual") -> None:
        """一键熔断：停止所有自动开仓，置 Valkey 标志。"""
    def resume(self) -> None: ...
    def is_halted(self) -> bool:
        """⚠️ 永远直读 Valkey 原子标志位，禁止在类内部维护 self._halted。
        多进程/Celery worker/进程重启后内存状态会丢失，内存缓存会让熔断形同虚设。"""
    # 分市场
    def check_global(self, account: str) -> RiskState: ...   # 总回撤/单日亏损
    def check_etf_conv(self, order: Order) -> RiskDecision: ...
    def check_crypto(self, order: Order) -> RiskDecision: ...
```

## 6. 风控规则

### 6.1 全局（系统级）
| 规则 | 默认 | 触发动作 |
|---|---|---|
| 一键熔断 | 手动按钮 | 停所有自动开仓（仅允许平仓） |
| 总回撤 | 回撤达阈值（如 -15%） | 强制暂停开仓 |
| 单日亏损 | 单日亏损超阈值 | 仅平仓不开新仓 |

### 6.2 场内（可转债/ETF）
| 规则 | 说明 |
|---|---|
| 单标的仓位上限 | 单可转债/ETF 不超账户 X% |
| 单笔最大亏损 | 触发止损 |
| 日内交易次数限制 | 防过度频繁磨损 |
| 严格止损 | 禁扛单 |

### 6.3 加密专属
| 规则 | 默认 |
|---|---|
| 杠杆上限 | 固定低杠杆（≤5x），策略不可超 |
| 逐仓隔离 | 单合约爆仓不扩散 |
| 插针行情防护 | 异常瞬时波动 → 自动撤单+暂停 |
| 爆仓预警 | 维持保证金率逼近 → 告警 |
| 单日亏损熔断 | 超阈值 → 仅平不开 |

规则全部配置驱动（PG `risk_rule` 表 + Valkey 热缓存），Web 可改，改完即时生效。

## 7. 数据流

```
策略.place_order ─> RiskControl.check_order(order, account)
  ├─ if is_halted() → rejected("熔断中") 
  ├─ check_global(account) → 回撤/单日亏损
  ├─ check_etf_conv / check_crypto(order) → 分市场规则
  └─ approved → 放行到 adapter；rejected → 丢弃 + risk_log + 告警
Web一键熔断 ─> emergency_halt() ─> Valkey halt=1 ─> 所有策略下次 check_order 即拒
定时监控 ─> 各账户回撤/亏损 ─> 触阈值自动降级 ─> 告警
```

## 8. 与其它模块交互

- **策略框架（02）**：`place_order` 前置 `check_order`，是硬接入点。
- **可转债ETF（04）/加密（05）**：下单链路必过风控。
- **Web 后台（08）**：`/api/risk/halt`、`/api/risk/resume`、规则配置页、风控看板。
- **告警（10）**：风控触发 `notify(level=critical)`。
- **数据中台**：算回撤需要持仓盈亏+行情。

## 9. 配置 schema

```json
{
  "global": {"max_drawdown":0.15,"daily_loss_limit":0.05},
  "etf_conv": {"single_position_pct":0.15,"max_trades_per_day":20,"strict_stop_loss":true},
  "crypto": {"leverage_max":5,"margin_mode":"isolated","pin_protection":true,"daily_loss_limit":0.05},
  "halted": false
}
```

## 10. 关键设计决策

| 决策 | 选择 | 理由 |
|---|---|---|
| 双层 | 全局+分市场 | 单点不扩散，分市场规则贴合各自风险 |
| 取严 | 策略配置与中心冲突取严 | 兜底，防策略误配 |
| 熔断状态 | Valkey 标志位，禁止内存缓存 | 高频读，所有策略/Celery worker/重启进程共享；单例内存会丢，必须直读 Valkey |
| A 股 | 不进 check_order | adapter 永远 raise，到不了风控 |
| 规则 | 配置驱动热生效 | 极端行情可即时调 |
