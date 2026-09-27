# 批 63 · Phase B 方案：EMQ 行情 + EMT 交易客户端（pybind11 编译绑定）

## 目标与选型

东财 EMT/EMQ 接入：EMQ 极速行情（`quote_api`）+ EMT 极速柜台交易（`emt_trader_api`）。**选型 pybind11 编译绑定**（已与用户确认，依据：C++ 虚函数回调 trampoline 天然匹配 / vnpy_xtp 先例 / struct 处理干净 / 性能）。

## 勘察结论（SDK `docs/reference/emt-sdks/emt_api/`，file:line 实证）

- **行情接口**：`EMQ::API::QuoteApi`——`CreateQuoteApi(log_path, file_level, console_level)` 工厂 → `RegisterSpi(QuoteSpi*)` → `Login(ip, port, user, pwd) → int32`（`<0` 失败）→ `SubscribeMarketData(char* tickers[], count, exchange_id)` / `UnSubscribeMarketData` / `SubscribeAllMarketData`。**无 OnConnected 回调**——连接状态=Login 返回值 + `OnError(EMTRspInfoStruct*)`。
- **回调 `QuoteSpi`**：核心 `OnDepthMarketData(EMTMarketDataStruct* md, int64_t bid1_qty[], bid1_count, max_bid1_count, ask1_qty[], ...)`；另有 `OnSubMarketData/OnUnSubMarketData/OnSubscribeAllMarketData/OnQueryLatestMarketData` 等（订阅确认/查询类）。
- **tick 结构 `EMTMarketDataStruct`**（emt_quote_struct.h:176-301）：`exchange_id`(SH=1/SZ=2/BJGZ=5) + `ticker[]` + `last_price/pre_close/open/high/low/close` + `upper/lower_limit_price` + **`data_time`(int64 YYYYMMDDHHMMSSsss)** + **`qty`/`turnover`（当日累计，股/元——与 XTP 同构，直接对 hub 差分语义）** + `bid[10]/ask[10]/bid_qty[10]/ask_qty[10]`（十档）+ `ticker_status[8]` + union 品类扩展。
- **编译链**（demo/CMakeLists.txt）：`-std=c++11 -pthread`；链接 `emt_api emt_quote_api emt_quote_api_lv2 emt_quote_api_bse fmt::fmt Threads`；include 依赖 **`/opt/services/trader_api/infra/tcp_framework/dist/spdlog/include`**（spdlog+fmt 头文件）。
- **demo 用法**（quote_demo.cpp）：`CreateQuoteApi("./logs", DEBUG, DEBUG)` → `RegisterSpi(this)` → `Login("61.152.230.216", 8093, user, pwd)` → `SubscribeMarketData(codes, n, EMQ_EXCHANGE_SZ)`。
- **账号**：EMQ 登录账号/密码（**已加密存 staging `external_interface` 的 emt_emq 行，勿在此明文**）；L1 行情 61.152.230.216:8093 / L2 61.129.116.188:9988；EMT 交易 61.152.230.41:19088。

## 里程碑（顺序，各自可验收）

1. **P1 编译链跑通**：本地 g++ + pybind11 编出 `emt_quote_api.cpython-3*.so`（绑定产物名 emt_*——代码现名 emd_quote_api 为既有实现，重命名随本批顺手对齐或保留，实施时定）（绑定 QuoteApi/QuoteSpi 最小面）。**关键未知**：spdlog/fmt 头文件依赖（EMQ 头文件是否 include spdlog；本地准备 header-only spdlog+fmt，或 SDK 头文件自带）。
2. **P2 `EmqMdGateway`**：Python 层对接批 63 的 `MdGateway` 抽象（connect/subscribe/unsubscribe/set_on_tick/connected/start_ready/poll_supervise），tick 映射 `EMTMarketDataStruct` → 兼容 TickData（`data_time`→tz-aware datetime、`exchange_id`→SSE/SZSE、十档取五、qty/turnover 当日累计）。
3. **P3 staging 真连 EMQ 收 tick**：staging 库配 `emt_emq` 行（东财凭证+地址）→ hub 按 provider 切到 EmqMdGateway → 收真实行情（M5 四键协议复用，切源=confirm target——现行 M5 机制，D26 终态将退役改换行语义）。
4. **P4 EMT 交易**：`emt_trader_api` 绑定 + `EmtAdapter`（ExecutionAdapter 子类）——**完整实现**（依赖序（2026-09-25 重组）：批61 M6（序列2）→ D26-A TD builder 注册表（序列3）→ 本批（序列4）——P4=_TD_BUILDERS 注册表第二实例）。实现步骤：①绑定 emt_trader_api（对齐 P1 模式，EMTMarketDataStruct 同族）②EmtAdapter 实现 send_order/cancel/query_position/query_account（ExecutionAdapter 契约，adapters.py:47）③注册 `_TD_BUILDERS["emt_emq"]` + `create_adapter` 映射（走 D26-A 下沉后的注册表）④staging 真连 EMT TD 下单/查询/对账链验证（凭证=emt_emq 行 EMT 交易身份，与 EMQ 行情身份分离）。

### P4 执行规格 v2（2026-09-26 双盲返修：A P0×1+P1×4 / B **实测级** P0×1+P1×5 同判全修——vnpy 形状契约立法/nodelete holder/默认参 lambda/砍 QueryOrders/市价钉 BEST5_OR_CANCEL；绑定目录勘误=P1/P2 产物实在 `emd/`，下文「文件结构」节同步已改）

**vnpy 形状契约（双审同判 P0 立法——EmtAdapter 的核心设计约束）**：worker 消费链（reconcile_orders/halt_edge_cancel/write_trade_log/snapshot_cycle，trading.py:59-303）全部假定 vnpy 形状——**EmtAdapter 必须在回调内合成 vnpy OrderData/TradeData/AccountData**（Status/Direction 枚举、vt_orderid=str(order_emt_id)、balance/frozen 字段）经 ee 推 EVENT_ORDER/EVENT_TRADE/EVENT_ACCOUNT，并维护 `_vt2cid`/`_lock` 同形属性+override get_vt_orderid。否则四链静默失效：对账空转/熔断沿撤单 no-op/成交一律记 SELL（Direction 硬比较）/account_snapshot 写零值（非空列表骗过 SB1 守卫=污染风控回撤）。**测试必须钉 Status 映射+事件合成（vnpy 形状断言非原生结构）**。

**SDK 关键事实（vendor/emt/include/emt_trader_api.h 实证+B 实测编译校准）**：
- `EMT::API::TraderApi`（符号 **CreateTraderApi** 非 CreateTraderAPI）：`CreateTraderApi(client_id:u8, save_path, log_level)` **单例**（重复调用返同句柄，B 运行实证）+析构 **protected**——`py::class_<TraderApi>` 默认 holder **编译必炸**（P1 reference policy 救不了注册期实例化；P1 QuoteApi 析构 public 故先例不可移植）——**必须 `std::unique_ptr<TraderApi, py::nodelete>` holder**（B 实测编译+dlopen+运行全过）；`Login(ip,port,user,pwd,sock_type,local_ip,terminal_info)`——**C++ 默认参不透传**（B 实测 5 参调用 TypeError+EMTUserTerminalInfoReq 未注册不可构造）——凡默认参/指针参方法（Login/QueryPosition/CreateTraderApi 第三参）**一律 lambda 包装**（nullptr/None→NULL 适配，EMT_LOG_LEVEL 枚举先绑）；`Login→session_id`（0=失败同步阻塞，绑定期 `py::call_guard<py::gil_scoped_release>`）；`InsertOrder(EMTOrderInsertInfo*, session_id)→order_emt_id`；`CancelOrder(order_emt_id, session_id)`；`Logout(session_id)`；查询族**异步分帧回调（request_id+is_last 收齐）**——**绑定面砍 QueryOrders**（签名要 EMTQueryOrderReq 可写请求结构+千条上限报错风险）**只留 QueryUnfinishedOrders**（无参结构，对账够用——halt_edge_cancel/reconcile 消费的本来就是在场单）+QueryPosition（lambda：ticker None→NULL 查全部+market 默认）/QueryAsset；`GetApiLastError()`；`SubscribePublicTopic(EMT_TERT_RESTART)` **每次 Login 前调**（含重登——SDK :719 注释：断线后不 Logout 直接 Login 公共流不生效）；**resume_type 钉 RESTART（当日全量重传）**——reconcile 成交补录半边（query_trades 当日成交，trading.py:303）依赖公共流重传喂事件缓存，QUICK=断线/重启窗成交永久静默丢失（快审 N1；故不绑 QueryTrades，OnQueryTrade 回调随之不绑=无悬空））；**不支持过夜**。
- **重登序列（双审同判）**：OnDisconnected（API 自动重连仅通知）→ **Logout(old_session)→SubscribePublicTopic→Login 取新 session_id**；Login=0 退避重试；旧 session_id 调用 fail-fast 拒单非静默。
- `TraderSpi` 回调：OnConnected/OnDisconnected/OnError(EMTRI)/OnOrderEvent（**部成不推**由成交回报确认；拒单=order_status REJECTED）/OnTradeEvent（quantity 本次非累计）/OnCancelOrderError(EMTOrderCancelInfo——结构清单补)/OnQuery{Order（UnfinishedOrders 响应同走此回调——SDK 无 OnQueryUnfinishedOrder）,Position,Asset}。**回调指针 SDK 所有**——回调内字段拷贝（EmqMdGateway._md_to_tick 先例）；spi 实例 adapter 自持引用防 GC。
- **ORDER_STATUS 实为 8 值**（规格 v1「六态」漏）：INIT/ALLTRADED/PARTTRADEDQUEUEING/NOTRADEQUEUEING/**PARTTRADEDNOTQUEUEING（部撤——SDK 符号 NOT 非 NOTRADE，qty_left 语义变为被撤量）**/CANCELED/REJECTED/UNKNOWN→vnpy Status 映射表全 8 值。
- `EMTOrderInsertInfo`：ticker[16]（非 64——strncpy 截断）/market/price/quantity int64/price_type/side(BUY=1/SELL=2 uint8 #define 非枚举——绑常量)/business_type CASH=0；order_client_id **uint32 数值**（XTP 字符串 client_id 放不进——EmtAdapter 自派生短号+字符串↔短号映射表）。
- **枚举映射陷阱**：交易 market **1=SZ_A/2=SH_A/5=BJ_A**（行情 1=SH/2=SZ 沪深对调——独立映射）；**市价六变体市场适用性互斥**（BEST5_OR_LIMIT 沪专/BEST_OR_CANCEL+ALL_OR_CANCEL 深专）——**Order.order_type=market 统一钉 EMT_PRICE_BEST5_OR_CANCEL（五档剩撤沪深通用——FORWARD_BEST 亦通用，选此对齐主流柜台缺省）**，limit=EMT_PRICE_LIMIT=1。
- Position 映射：frozen=total_qty-sellable_qty、yd_volume=yesterday_position、pnl=0（SDK 保留字段恒 0，对账注意）；AccountData：balance=total_asset、frozen=total_asset-buying_power、available=buying_power。
- 编译链：**零新增链接依赖**（-lemt_api 已含 TraderApi 符号，nm+B 编译实证）；EMQ_OVERRIDE_GUARDED 宏 TU-local——**抽 `emd/bind_common.h` 公共头**（两 cpp 共用防漂移）；build.sh 双目标产物 `emt_trader_api${EXT_SUFFIX}`（与 vendor libemt_trader_api_c.so 的 C 风格符号不交叠，B 实测）；部署链自动跟（wrapper 跑 build.sh 整体）。

**改动面（4 改+2 新+1 头）**：
1. **新 `emd/bind_trader.cpp`**（+# `emd/bind_common.h` 抽宏）：TraderApi 绑定（nodelete holder/lambda 包装默认参/名如上清单）+TraderSpi trampoline（宏共用）+结构体（InsertInfo def_readwrite+ticker 拷贝辅助/回报五结构只读/EMTRI）+枚举+常量。request_id Python 侧自增。
2. **改 `emd/build.sh`**：第二编译目标（同链接行）。
3. **改 `adapters.py`**：`EmtAdapter(ExecutionAdapter)`——**vnpy 形状合成**（上立法）+XTP 模式对齐（事件缓存/锁/稳定窗）+EMT 特有（session_id 重登序列/分帧聚合 get_vt_orderid override/短号映射/market 独立映射/单例防御：模块级持 api 句柄防二次 Create）；**凭证组装具名函数**：`get_interface_row(row_id=account_id)`（broker.py:207 解密 fail-fast，不加 EmtBroker）+`_parse_host_port(emt_td_host, default_port=19088)`（md_gateway.py 先例）；零参构造支持（gateway=None→mock 返回 id，XTPAdapter 惯例）。create_adapter mapping 加 `"emt_emq"`。
4. **改 `td_registry.py`**（立法：builder 落本模块）：`@register_td_builder("emt_emq")`——EmtAdapter 兼任 gw/td_api（connect(setting)=Login；connect_status 属性；[gw] 会话日志通道：adapter 内 logger 直写对齐 EVENT_LOG 语义）——八键 dict；td_open=复用 A 股连接窗（**配置键 xtp_session_lead/lag_min 从此双 provider 共用——知情接受注记**）；client_id 派生写死 `(tid-1)%200+20`（u8 域 20-219 避 XTP 2-99 段）；**EOD 处置**：adapter 事件循环检测交易日变更→**先清模块级单例句柄→Release→Create→Subscribe→Login**（SDK :665 Release 后再 Create 得新句柄——与单例防御的交接步骤，快审 N2）（「不支持过夜」——worker 跨夜存活是 XTP 窗设计前提故不能靠单元重启，A-P1-3）。
5. **新 `tests/test_emt_adapter.py`**：fake 绑定模块（test_emq_md_gateway 先例）+__new__ 绕构造——**Status 8 值映射/Direction 映射/vnpy 形状合成断言（事件推流+balance/frozen 字段）/分帧 is_last 收齐/重登序列（含 Logout→Subscribe→Login 次序）/短号映射/market 对调映射/单例防御/**EOD 重建序列/session 失效 fail-fast 拒单/部撤 qty_left 语义**。
6. **改 `tests/test_td_registry.py`**：emt builder 形状钉（八键+cfg_adapter="emt_emq"）。

**staging 真连验证（步骤④，外部依赖拆两步）**：④a 凭证落位——staging emt_emq 行（id=6）的 emt_account/emt_password/emt_td_host 现缺（批 61 演练 L1 拒实证），**需用户提供测试账号值**经管理面 PUT 写入；④b 真连——Login→下单（测试小额/撤单）→查询分帧收齐→对账链（vnpy 形状合成验证）。**编码/单测不因 ④a 阻塞**。

**P4 风险清单**：单例句柄（nodelete+模块级持有防二次 Create）；session_id 失效窗口（fail-fast 拒单）；部成不推（query 快照+OnTradeEvent 累计）；client_id u8 撞号（派生式+文档警示）；回调指针生命周期（帧内拷贝）；QueryPosition ticker 非空不触发回调的 SDK 陷阱（只全市场查）；千条上限（QueryUnfinishedOrders 场景天然小集，知情）。

## 文件结构

- `server/vendor/emt/`：SDK 头文件 + `lib/*.so`（无 linux 子层）（编译/部署资产；git 忽略，部署时放服务器）
- `server/src/strategy_framework/emd/`：C++ pybind11 绑定源码（**勘误 2026-09-26：P1/P2 产物实际落此目录（emd，非笔误后重建 emt/）——bind_quote.cpp+bind_trader.cpp+bind_common.h（宏公共头）+ build.sh 编译脚本**（可复现，本地 3.10/服务器 3.11 各自 venv 重编））
- `server/src/strategy_framework/md_gateway.py`：**`EmqMdGateway(MdGateway)` 与 XtpMdGateway 同文件**（单文件不拆包——两个网关实现共 ~300 行，简洁优先；P1 决策）

## 关键映射（tick 契约，对齐 md_gateway.py docstring）

| EMQ | hub tick 契约 |
|---|---|
| `ticker` + `exchange_id` | `symbol`（SH→`SHSE`/SZ→`SZSE`/BJGZ→`BSE`；`_project_symbol` 做 SSE→SHSE 归一） |
| `data_time`(int64) | `datetime`（YYYYMMDDHHMMSSsss → tz-aware UTC） |
| `last_price` | last_price |
| `qty`/`turnover` | volume/amount（当日累计，MinuteAggregator 差分） |
| `pre_close/open/high/low/close` | 同名 |
| `upper/lower_limit_price` | limit_up/limit_down |
| `bid[0:5]/ask[0:5]/bid_qty[0:5]/ask_qty[0:5]` | bid/ask 五档（结构是十档） |
| `exchange.value` 语义 | 用 SDK 的 EMQ_EXCHANGE_TYPE 枚举（SH/SZ），非 vnpy 枚举——EmqMdGateway 内自己做枚举→vnpy Exchange 映射 |

## 编译链难点（P1 首要解决）

- **spdlog/fmt 依赖**：demo CMake 指向东财环境绝对路径 `/opt/services/trader_api/...`。需确认 EMQ 头文件是否 include spdlog（决定编译是否必需），本地准备 header-only spdlog + fmt。
- **Python ABI**：服务器 Python 3.11 / 本地 3.10，pybind11 绑定 .so 按解释器版本编译（`cpython-311` vs `cpython-310`），部署到服务器须服务器 venv 编译或 ABI 对齐。
- **命名空间**：`EMQ::API::QuoteApi`、`EMQ::API::QuoteSpi`（绑定需用全限定名）。

## 不在本批

- EMT 交易的完整下单状态机（M6 交易门面）
- Lv2/北交所行情接入（先 L1 标准行情，跑通后扩展）
- 东财 XTP 同账户并存的双源切换实测（⚠️ D26 终态=换行验证非 M5 confirm——届时按 D26 §4.2 语义执行）（等 P3 跑通后）

## 验证

1. P1：本地编译出绑定 .so + Python 能 import + CreateQuoteApi 返回非空。
2. P2：单测（mock 绑定层）——EmqMdGateway 对接 MdGateway 全方法、tick 映射正确（data_time 转换/交易所映射/五档截取）。
3. P3：staging 真连 EMQ 收 tick（盘前窗开验证 OnDepthMarketData 触发 → hub 聚合出 bar）。
4. 全量 pytest 不降。
