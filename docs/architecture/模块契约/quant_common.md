# 模块契约 · quant_common(层 0 底座)

> 层 0:全系统共享基础,禁业务依赖、禁 import 上层(test_layering 守门)。

## public API
- `crypto.py`:encrypt/decrypt/mask(Fernet)
- `eventbus.py`:站内事件总线(批14 SSE)
- `terms.py`:i18n 条款注册表
- **`markets.py`(批55-0)**:维度注册表单源——MARKETS/EXCHANGES(含市场归属)/ASSET_CATEGORIES/DATA_CATEGORIES/CAPABILITIES/SYNC_ID_CAP_MAP(sync_id↔枚举映射)/NON_DATA_PROVIDERS/MARKET_OP_DECOMP(权限五键→(market,category,venue))。**纯数据零 import**;能力查询/⊆校验在 data_platform/capabilities.py(读 _ADAPTERS 属上层)。

## 依赖
无(层 0)。

## 被调
全系统。markets.py 消费方:perm_registry(未来)/55a 端点/前端经 GET 下发。

## 不变量
- 纯数据+纯函数;禁 import src.* 上层模块(test_layering 断言)
- 加键=注册表加行(市场/品类/交易所/能力枚举增长受代码消费驱动)

## 修订记录
- 2026-09-19 初版(批55-0 建 markets.py;立法见 27 号文档)
