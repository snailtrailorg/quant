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

## 文件结构

- `server/vendor/emt/`：SDK 头文件 + `lib/linux/*.so`（编译/部署资产；git 忽略，部署时放服务器）
- `server/src/strategy_framework/emt/`：C++ pybind11 绑定源码（原笔误 emd——与 SDK 目录 vendor/emt 对齐）（`bind_quote.cpp` trampoline 内联）+ **`build.sh` 编译脚本**（可复现，本地 3.10/服务器 3.11 各自 venv 重编）
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
