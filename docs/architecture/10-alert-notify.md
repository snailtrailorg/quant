# 10 - 告警/通知

> **平台化集成（2026-08-08）**：MessageChannel 接口（PT4，src/alert_notify/channel.py + channel_config 表），WechatWork/Discord/ServerChan 实现，别人加钉钉/邮件实现接口。详见记忆 `platform-architecture`。

## 1. 目的

平台对外触达的唯一出口：异常告警、风控触发、盘后报告分发。把推送渠道（微信/Discord）与触发源解耦，统一一层入口。

## 2. 职责

1. **统一入口**：各模块调 `notify(level, title, body)`，不直接接触推送渠道。
2. **分级路由**：critical 即时推送，warn 汇总，info 汇总或存库。
3. **多渠道**：微信（Server 酱 / 企业微信机器人）、Discord Webhook。
4. **去重与限流**：同类告警短时间去重，防刷屏。
5. **盘后报告分发**：调度层生成的报告推送到指定渠道。
6. **告警历史**：存 PG `alert` 表，Web 展示。

## 3. 边界与非目标

- **不做**：交易逻辑、风控判断（只转发结果）。
- **非目标**：不做电话/短信（成本高，微信足够）；不做多用户分发（个人平台）。

## 4. 依赖

- 微信：Server 酱 / 企业微信群机器人 Webhook（HTTP）
- Discord：Webhook（HTTP）
- `httpx`（推送）
- PostgreSQL（告警历史）
- Valkey（去重计数，可选）

## 5. 接口

```python
Level = Literal["info", "warn", "critical"]

class AlertNotify:
    _instance = None
    @classmethod
    def get(cls) -> "AlertNotify": ...

    def notify(self, level: Level, title: str, body: str,
               channel: str | None = None) -> str:
        """channel=None 按级别路由；返回 alert_id。"""
    def report(self, title: str, body: str, channel: str = "wechat") -> None:
        """盘后报告分发（info 级，完整内容）。"""
```

## 6. 分级路由

| 级别 | 渠道 | 时效 | 去重 |
|---|---|---|---|
| critical | 微信 + Discord 即时 | 立即 | 1min 内同标题合并 |
| warn | 微信 | 5min 汇总 | 合并 |
| info | 存库 | 不推或每日汇总 | — |

配置驱动（PG `alert_rule`）：可按来源模块/关键词/级别指定渠道。

## 7. 触发源

| 模块 | 触发 |
|---|---|
| 风控中心（07） | 熔断/单日亏损/爆仓/插针防护 → critical |
| 交易引擎（04/05） | 止损/异常/策略崩溃 → warn/critical |
| A 股分析（03） | 关键选股信号/评级变化 → info |
| 调度层（09） | 盘后报告 → report；任务失败 → warn |
| LLM 网关（01） | provider 全部不可用 → critical |
| 数据中台（06） | 数据源失败/异常价格 → warn |

## 7.5 多语言消息

notify() 传 `lang="zh"|"en"` 参数，告警消息按用户语言偏好输出。语言来源：Web 用户配置 → 检测 `Accept-Language` → 默认英文。`info` 级别消息的标题/摘要也受语言影响。

## 8. 去重、限流与配额

- 短时间窗口（1min）内相同 `title+来源` 的告警合并计数，body 追加，不重复推。
- 全局限流：单渠道每分钟最多 N 条，超限降级为汇总。
- **配额感知**：Server 酱免费版有日配额（critical 刷屏会顶配额，导致后续爆仓预警推不出）。策略：critical 也走"30min 聚合打包"检查剩余配额；**优先用企业微信群机器人 / Discord Webhook**（配额宽松）作主渠道，Server酱备用。
- 状态用 Valkey 计数器（含各渠道当日已用配额）。

## 9. 与其它模块交互

- **风控（07）**：critical 即时推送。
- **交易引擎（04/05）**：止损/异常推送。
- **调度层（09）**：盘后报告 `report()` 分发；任务失败 `notify(warn)`。
- **Web 后台（08）**：`/api/alert` 展示历史。

## 10. 关键设计决策

| 决策 | 选择 | 理由 |
|---|---|---|
| 统一入口 | `notify()` 一层 | 各模块不耦合渠道 |
| 渠道 | 企业微信机器人+Discord 为主，Server酱备用 | 配额宽松，Server酱免费版有日限额会顶配额 |
| 分级路由 | critical即时/warn汇总/info存库 | 防刷屏又保关键 |
| 去重 | 窗口合并 | 同类告警不刷屏 |
| 历史 | 存 PG | Web 展示 |
