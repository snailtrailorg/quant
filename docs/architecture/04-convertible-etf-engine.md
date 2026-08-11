# 04 - 可转债/ETF T+0 引擎

## 1. 目的

可转债 + 场内 ETF 的全自动 T+0 程序化交易。底层用 vnpy 核心 + vnpy_xtp（中泰证券 XTP，Linux 原生），本模块负责**策略与配置层**——把策略框架（02）的统一抽象接到 vnpy 执行内核。

## 2. 职责

1. **策略实例管理**：可转债双低轮动、日内均值回归、溢价套利、网格；ETF 指数趋势、日内波段、配对对冲。
2. **配置驱动**：Web 端配置策略（因子+权重+风控参数），复用 02 schema。
3. **执行接入**：通过 `XTPAdapter` 把统一 `Order` 翻译成 vnpy_xtp 下单请求。
4. **持仓/订单/盈亏上报**：实时写 Valkey + PG，供 Web 实盘看板。
5. **T+0 执行语义**：当日买卖、撤单、止损、调仓，处理涨跌停与临停。

## 3. 边界与非目标

- **底层 vnpy/vnpy_xtp 是第三方成熟组件**，本模块只在配置层、适配层、策略层自建。
- **不做**：A 股个股 T+1 交易（A 股模块只读）；可转债转股/回售操作（仅交易层面，条款解读在 03+LLM）。
- **待券商确认**：中泰 XTP 门槛/品种放行/费率，确认后回填配置与回测费率参数。

## 4. 依赖

- 策略框架（02）：Strategy、Factor、`XTPAdapter`
- vnpy 核心 + vnpy_xtp（第三方）
- 数据中台（06）：可转债/ETF 日线+分钟线、可转债条款
- 风控中心（07）：下单前置校验、单标的仓位/日内次数限制
- 调度层（09）：定时调仓触发
- 告警（10）：止损/异常/熔断推送

## 5. 接口

### 5.1 XTPAdapter
```python
class XTPAdapter(ExecutionAdapter):
    def __init__(self, xtp_gateway: vnpy_xtp.XtpGateway): ...
    @staticmethod
    def parse_vt_symbol(vt_symbol: str) -> tuple[str, str]:
        """vt_symbol `603986.SHSE` → ('603986', Exchange.SHSE)。
        拆分后 symbol='603986' + exchange=SHSE 喂 XTP 网关，
        否则直接传 vt_symbol 会'未知合约'拒单。"""
    def send_order(self, order: Order) -> str:
        symbol, exchange = self.parse_vt_symbol(order.vt_symbol)
        req = self._to_vnpy_request(order, symbol, exchange)  # 统一 Order → vnpy SendOrderRequest
        return self.xtp_gateway.send_order(req)
    def cancel_order(self, vt_orderid: str): ...
    def query_position(self) -> list[Position]: ...
```

### 5.2 策略类型注册
```python
@register_strategy("convertible_doublelow", base=Strategy, adapter="xtp")
class ConvertibleDoubleLow: ...      # 双低轮动

@register_strategy("convertible_grid", base=Strategy, adapter="xtp")
class ConvertibleGrid: ...          # 网格

@register_strategy("etf_trend", base=Strategy, adapter="xtp")
class ETFTrend: ...                 # 指数趋势
```

### 5.3 可转债专属因子（02 注册制，category=convertible）
- `double_low`（价格+溢价率，双低核心）
- `premium_rate`（转股溢价率）
- `conv_value_dev`（转股价值偏离）
- `dsl:my_premium`（DSL 自定义）

## 6. 数据流

```
数据中台 ─> 可转债行情+条款 ─> 策略.on_bar ─> 因子 ─> 信号
  └> 风控.check_order ─> XTPAdapter.send_order ─> vnpy_xtp ─> 中泰柜台
  <─ 成交回报 ─> 更新持仓 ─> Valkey实时 + PG持久化 ─> Web实盘看板
调度层(定时调仓) ─> 触发策略 rebalance ─> 同上下单链路
LLM网关 ─> 可转债条款解读(强赎/下修/回售) ─> 影响因子(如触发强赎剔除)
```

## 7. T+0 执行语义

- 当日可买卖，无 T+1 限制。
- 涨跌停/临停：`XTPAdapter` 收到拒绝回报 → 策略转"等待/撤单"，不过度重试。
- 交易时段：9:30-11:30 / 13:00-15:00 连续竞价，非时段策略休眠（节省算力，与 09 调度配合）。
- **集合竞价**：9:15-9:25 为集合竞价，撮合逻辑不同；9:20-9:25 不可撤单。**策略启动时间过滤：非连续竞价时段禁止开仓**（`on_bar`/`on_tick` 判断时段，9:30 前不下单，9:20-9:25 不撤单）。
- 撤单：策略 `on_bar` 判断条件消失 → `cancel_order`（注意 9:20-9:25 集合竞价不可撤单窗口）。

## 8. 配置 schema（复用 02，type=convertible_t0 / etf_t0）

见 02 文档第 6 节示例。风控参数（止损/单标的仓位/日内最大交易次数）写入策略配置，同时受 07 风控中心全局规则约束（取严）。

## 9. 与其它模块交互

- **风控中心**：每笔单 `check_order`；`emergency_halt` 后停开新仓、只平不开。
- **数据中台**：行情、可转债条款；回测时历史数据，实盘实时行情同 schema。
- **LLM 网关**：条款解读（强赎/下修触发判断）喂因子，不直接下单。
- **Web 后台**：启停策略、改参数、看持仓盈亏。
- **回测**：自建 BacktestEngine（纯 Python），schema 对齐，20 天等待期结果零迁移切 live。

## 10. 关键设计决策

| 决策 | 选择 | 理由 |
|---|---|---|
| 实盘通道 | 中泰 XTP + vnpy_xtp | 国内唯一 Linux 原生证券交易，不用 Windows |
| 策略代码层 | 复用 02 统一基类 | 三市场一致，配置驱动 |
| 适配层 | XTPAdapter 翻译统一 Order→vnpy 请求 | 隔离 vnpy 变更 |
| 回测/实盘 schema 对齐 | 现在 Tushare/AkShare 数据 schema 就对齐 XTP 实时 | 零迁移 |
| 费率建模 | 分市场、注意沪深差异+最低佣金+ETF免5 | 回测贴近实盘 |

## 11. Linux 编译与踩坑指南（Alibaba Cloud Linux 3（OpenAnolis/al8/RHEL8 系））

vnpy_xtp 包装中泰 XTP C++ SDK（`libxtptraderapi.so`），在 Alibaba Cloud Linux 3（OpenAnolis/al8/RHEL8 系） 上安装需注意：

```bash
# 1. 装编译依赖（Alibaba Cloud Linux 3（OpenAnolis/al8/RHEL8 系） 用 dnf）
sudo dnf install -y gcc-c++ make openssl-devel python3.10-devel

# 2. libstdc++ 版本要匹配 XTP SDK 编译时依赖的 GLIBCXX
#    若运行时报 "GLIBCXX_3.4.XX not found"：
#    - 确认 XTP SDK 的 .so 是为 RHEL/Alibaba Cloud Linux 系编译（非 Ubuntu 版）
#    - 必要时用对应版本的 .so，或在与部署机同环境上重编 vnpy_xtp
ldconfig -p | grep libstdc++.so.6   # 查可用 GLIBCXX 版本
strings /usr/lib64/libstdc++.so.6 | grep GLIBCXX

# 3. XTP SDK .so 放置 + LD_LIBRARY_PATH（或写进 systemd Environment=）
export XTP_LIB_DIR=/opt/xtp/lib
sudo sh -c "echo $XTP_LIB_DIR > /etc/ld.so.conf.d/xtp.conf && ldconfig"
# 或在 systemd unit 里设 Environment=LD_LIBRARY_PATH=/opt/xtp/lib
```

**关键原则**：XTP `.so` 必须与**部署机同 glibc/libstdc++ 环境**构建。开发机是 Fedora（与 Alibaba Cloud Linux 3 同 dnf/RPM 系，glibc 兼容性好），但**最终 .so 必须在 Alibaba Cloud Linux 3（OpenAnolis/al8/RHEL8 系） 上验证**，不能把 Ubuntu 编译的 .so 直接拿到 Alibaba Cloud Linux 3 跑。中泰提供的 SDK 若分 CentOS/Ubuntu 版，选 CentOS 版（Alibaba Cloud Linux 3 与 RHEL 系兼容）。

待中泰确认 XTP SDK 具体分发形态后，本节回填实际 .so 路径与版本号。
