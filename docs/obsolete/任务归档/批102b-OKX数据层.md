# 批 102b · OKX 数据层（多加密市场共存的验证批）

> 立项 2026-10-06（威廉姆：代理能力就绪后接第二个加密所，验证「同一 kind 多源共存」）。来源＝
> 101b 落地后 `bar_1d` 将同时承载两所 crypto 标的的**设计风险**（表拆分读点纪律）。
> 关联：批 102a（**前置，已完成**）· 设计真源 `flow/方案/多市场数据接入与代理体系-设计-20261006.md` **§四**

## 交付状态（2026-10-06）

| 段 | 状态 |
|---|---|
| 代码（adapter / 三注册表 / 泛化 handler / 迁移 0136） | ✅ 完成（上产 `202610061506-96acbca`） |
| mock 单测（`test_batch102b_okx.py` **67 例**）+ 往返用例 S/T/**U/V** + 全量 pytest **2167** 绿 + ruff + 令牌/词条门 | ✅ 完成 |
| dev 真库 `alembic upgrade head` → **0137**（含 downgrade 复核） | ✅ 完成 |
| **prod 真机尾段（六步，见「验收标准」末）** | ✅ **已执行**（prod 真机取证；唯一残项＝probe `Connection reset`，已挂待办） |
| `sync_config.start_floor` 回填 | ✅ 实测 **2020-01-01** → 迁移 **0137**，已上产 prod（release `202610061532-f5d0e55`；prod 读回 `start_floor=20200101`） |
| prod 五证 | ✅ 双链/`RELEASE`=`202610061532-f5d0e55`；`healthz=200`/`readyz=200`/`_probe ok:true`（含 `hub_hb in_set=4 fresh`）；迁移 0136+0137 在部署树；`PLAY RECAP failed=0 unreachable=0` |

## 目标

一句话：让 `okx` 成为第二个加密**数据源**（0 密钥公共行情），把 OKX 永续日线拉进 `bar_1d`，
标的后缀 `.OKX`；做完后「同一 kind 多源共存」在真库上被验证（两所标的互不干扰）。

## 现状（立项时实证 · 供存档）

| # | 事实 | 锚点 |
|---|---|---|
| 1 | `data_platform/adapters/` 有 `binance_adapter.py`（101/101b 真接）与 `joinquant_adapter.py`（103b）；**无 `okx_adapter.py`** | `ls server/src/data_platform/adapters/` |
| 2 | `interfaces/` 已有 **`okx_perp.py`＝交易通道**（api_key/api_secret/passphrase 三凭证）——**与数据源是两把键**，勿混 | `interfaces/okx_perp.py:1-20` |
| 3 | `data_source._REGISTRY` 已含 `"binance": BinanceDataSource`（0 密钥，`DEFAULT_RATE_LIMITS={}`）；**无 okx 行** | `data_platform/data_source.py:315-317` |
| 4 | `markets.PROVIDER_MARKET` 已有 `"binance": "crypto"`、`"okx_perp": "crypto"`；**无 `"okx"`** | `quant_common/markets.py:117-125` |
| 5 | `markets.SYNC_ID_CAP_MAP` 无 `okx_perp_daily`；该表键集须 ⊇ `sync_config.id ∪ sync_kind_config.sync_id`（批 99 起钉完备性） | `quant_common/markets.py:78-95` |
| 6 | `security_master._market_of_suffix` 已认 `"BINANCE"`/`"OKX"` → crypto ⇒ `.OKX` 后缀**无需新代码** | `data_platform/security_master.py:153-157` |
| 7 | `to_vt_symbol("BTC-USDT-SWAP.OKX")` → 原样（OKX 不在 `TS_EXCHANGE_MAP`，大写直通）；`-` 不是分隔符 | `data_platform/schema.py:47-60` |
| 8 | **`scripts/behavior_equiv_sync.py` 已于批 72 退役**（A/B 对照对象已删、`sync_kind_routing` 键失效，脚本自述「跑不出有意义结果，保留仅供考古」）⇒ 设计 §4.5 要补的 `_CATEGORY_TABLE` perp 过滤**落在死代码上**，本批**不做**（设计该条同批改写为「日后恢复该脚本时的必补项」） | `scripts/behavior_equiv_sync.py:1-4` |
| 9 | `bar_1d` 的**全表 DELETE** 仅存在于 `_make_full_rebuild_handler`（namechange/concept 族，**非 bar 表**）；bar 侧删除都是 `WHERE symbol=…`（`engine.py:2119/2149`）或在 A 股静态表范围内（`engine.py:886-887` 显式 `symbol IN (… asset_static_info)`）⇒ **两所共存不会互相误删** | `engine.py:1560` / `:2119` / `:2149` / `:886` |
| 10 | `web_api/routes/stock.py:198` 对 `bar_1d` 取「最新一日 close」，但**外层 JOIN `cb_basic_info` 限定 A 股转债正股** ⇒ crypto 行不会串进该结果集 | `web_api/routes/stock.py:194-201` |
| 11 | **dev 机出不到 OKX**：直连 `www.okx.com` 超时（curl exit 124）；经 AWS 代理 `snailtrail.org:12345` **TCP 不可连**（DNS 正常 `54.248.171.145`、443 通，唯 12345 不可达）⇒ 本批**无法做 dev 端到端实证**（威廉姆 2026-10-06 裁定：写码+mock，真机留 prod 实测） | 本机 `curl` / `/dev/tcp` 实测 |
| 12 | 101b 的 handler 工厂范式：`_BINANCE_BAR_SPECS: sync_id → (kind, sub_kind, freq, 首跑天数)` + `_make_binance_bar_handler`，逐 sync_id 生成 `_HANDLERS[<id>]` | `engine.py:952-1071` |

> **待验 → 已验（2026-10-06 prod 真机；结论文档见设计 §4.6）**：
> ① 历史深度＝**2020-01-01 起**（OKX `history-candles` 的**保留窗**，2019 全空；**`instruments.listTime` 不是下界**）
>    ⇒ `start_floor='2020-01-01'`（迁移 **0137**）；
> ② `bar=1Dutc` **被 API 接受**（`1D` ⟷ `1Dutc` 的 ts 差 28800000ms＝正好 8h）⇒ **无需 `1Hutc` 备选**；
> ③ 限频：全窗 485 标的 / **940s（≈1.94s/标的）**，**失败项里无 `code=50011`**（限频业务码）⇒ 未见限频退避风暴；
>    但出口侧有 **7/485 标的**（≈1.4%）在跑完 3 次退避后仍被 `ConnectionReset` ⇒ **重试预算偏薄**（已挂待办）；
> ④ `state` 收窄**仍未做**（证据不足，保持只按 `settleCcy` 过滤）——本轮 7 个失败标的（EDGE/LGELECTRONICS/MEGA/
>    UP/UVXY 等）恰是早期/低流动性类目，**可作「是否该按 `state` 收窄」的后续证据**。

## 依赖（就绪）

- 批 102a ✅（绑定面与注入能力：`configure_exit` + `engine._apply_exit_config` + `probe` 端点）。
- 迁移 0135 ✅（`proxy_config`/`proxy_binding` 两表已在 dev 与 main）。

## 引用（设计 / 方案）

- `flow/方案/多市场数据接入与代理体系-设计-20261006.md` **§四** —— 两把键与三注册表、枚举/历史端点、
  语义对齐三处、行形状与落库（**本任务遵循它，设计取舍不复述**）。
- `flow/规范/异常处理规范.md` —— 拉取失败的可见性要求（失败须进 `sync_log`，不许静默）。

## 产出

| 面 | 文件 | 内容 |
|---|---|---|
| adapter | `server/src/data_platform/adapters/okx_adapter.py`（新） | 枚举 + 分页历史 + 限频退避 + 字段映射 + `to_bar_rows`；`supports_exit_config=True` |
| 注册 | `adapters/__init__.py`（显式 import）· `data_source.py`（`OkxDataSource`）· `interfaces/okx.py`（新，0 密钥）· `interfaces/base.py`（`_PROVIDER_MODULES` 加 `"okx"`） | 三注册表 + 显式 import **四处齐**；缺一即「清单有·实现无」 |
| 词表 | `quant_common/markets.py` | `PROVIDER_MARKET['okx']='crypto'`；`SYNC_ID_CAP_MAP['okx_perp_daily']='hist_quote'` |
| 复用 | `data_platform/proxy.py` | `proxies_map()` **真源**（原散在 `binance_adapter._proxies`）——okx 与后续每个自持 HTTP 的 adapter 共用 |
| 迁移 | `server/migrations/versions/0136_okx_perp_daily.py`（新） | `sync_config` 一行 + `sync_kind_config` 一行（幂等 `ON CONFLICT DO NOTHING`） |
| 引擎 | `server/src/data_sync/engine.py` | **工厂泛化** `_make_binance_bar_handler` → `_make_crypto_bar_handler(..., *, label, enum_hint)` + `_sync_okx_perp_daily` + `_HANDLERS` 登记 |
| 前端 | `web/src/locales/index.js` | `integrations.provider.okx`（zh/en 对称）——数据源页签显示名；**其余零改动**（下拉/代理消费方列表均后端派生） |
| 测试 | `server/tests/test_batch102b_okx.py`（新，60 例）· `scripts/test-migration-roundtrip.sh` 用例 **S/T** · 连带字面量闸门随批更新（`test_batch99` / `test_sync_config_coverage` / `test_config_plane`） | 见 §验收标准 |

## 限定范围

- **只改**：上表文件 + 往返脚本（用例 S/T） + 三个**字面量连带闸门**（`test_batch99` / `test_sync_config_coverage` / `test_config_plane`——新 sync_id/provider 天然使这些字面量增长，是**预期更新**非回归） + 设计 §四（§重写制：改成与实现自洽） + `flow/待办.md`/`flow/进展`。
- **明确不碰**：① `interfaces/okx_perp.py`（交易通道凭证桩，属下单面）；② 实时/tick 腿与 md_hub（留 102c）；
  ③ tushare/聚宽/币安 adapter 的既有逻辑（**唯一例外**：`_make_binance_bar_handler` 泛化重命名——纯重构，
     币安行为零变化，`test_batch101`/`_101b` 原有断言即回归钉）；④ `sync_kind_config` 的既有行为（只增一行）；
  ⑤ **`scripts/behavior_equiv_sync.py` 不动**——该脚本批 72 已退役（自述「跑不出有意义结果」），
     设计 §4.5 的 `_CATEGORY_TABLE` perp 过滤落在死代码上，改它=往死构件上贴补丁；设计该条**已同批改写**为
     「日后若恢复该演练脚本，这条过滤是必补项」。
  ⑥ 不做 `1Hutc` 聚合兜底（仅在 prod 实测证明 `1Dutc` 不可用时才作为补救，本批先按 `1Dutc` 实现）。

## 接口契约

**新增 adapter**（`data_platform/adapters/okx_adapter.py`）：

```python
_BASE = "https://www.okx.com"        # 缺省基址；可由 proxy_binding.endpoint_override 覆盖
_INSTRUMENTS = "/api/v5/public/instruments"      # ?instType=SWAP
_CANDLES = "/api/v5/market/history-candles"      # ?instId=&bar=1Dutc&after=&limit=100
_RATE_MAX, _RATE_WINDOW = 20, 2.0    # IP 级 20 req / 2s（客户端滚动窗口节流；代理出口 IP 共享）
_MAX_PAGES = 600                     # 防御上限 + 触顶告警（静默截断＝静默数据洞）
_RETRY_BASE, _MAX_RETRY = 2.0, 3     # 退避 2/4/8s
_RETRYABLE_CODE = "50011"            # 业务层限频（HTTP 仍 200）

@register_adapter
class OkxAdapter(BaseDataAdapter):
    provider = "okx"
    capabilities = {"okx_perp_daily"}
    capability_decls = [CapabilityDecl("bar_daily", "historical", CRYPTO_ALL)]   # 非聚合域 ⇒ sub_kinds 空
    supports_exit_config = True      # 自己发 HTTP ⇒ 可配代理（102a）

    def list_symbols(self, refresh: bool = False) -> list[str]:
        """→ 全部 USDT 结算永续的 instId（如 'BTC-USDT-SWAP'）。过滤 settleCcy=='USDT'。实例级缓存。"""

    def _request(self, path: str, params: dict) -> dict:
        """GET（经 requests，带 `proxies_map(proxy)`）→ 校验 `code=='0'`，否则**抛**；
        网络异常 / 429·5xx（优先 Retry-After）/ `code=50011` → 退避重试；**绝不静默返空帧**。"""

    def pull_daily(self, symbol: str, start: str, end: str, adj=None, kind: str = "crypto") -> pd.DataFrame:
        """per-symbol 日线（`after` 游标分页，100 根/请求；窗口外行剔除 + ts 去重）。"""

    def to_bar_rows(self, df: pd.DataFrame, freq: str, adj_map: dict | None = None) -> list[tuple]:
        """→ 统一 11 字段。volume=volCcy（币量）、amount=volQuote、adj_factor=None、source='okx'、
        symbol=<instId>.OKX、ts=open_time UTC aware；只取 confirm=='1'。"""

    def fetch_supply(self, kind: str, sub_kind: str | None = None, **params) -> pd.DataFrame:
        """批 100 端口：('bar_daily','perp') → pull_daily(symbol, start, end)。"""

    def pull_minute(self, symbol, freq, start, end) -> pd.DataFrame:
        """本批不做盘中 bar ⇒ **响亮** UnsupportedFeature（不返空帧）。"""
```

**新增 DataSource**（`data_source.py`）：

```python
class OkxDataSource(DataSource):
    provider = "okx"
    DEFAULT_RATE_LIMITS: dict[str, float] = {}    # IP 级节流在 adapter 内自持（不接 rate_limit_context，
                                                  # 防 _get_rate_ds 回落 tushare 兜底源把 OKX 计进 tushare 熔断器）
    def get_client(self): return None
    def test_connection(self) -> bool: ...        # GET public/instruments?instType=SWAP，**经该消费方的出口配置**
                                                  # （裸直连在 prod 恒 False ⇒ 会把「配好代理即可用」误报成不可用）
```

**新增 Interface**（`interfaces/okx.py`，与 `interfaces/binance.py` 同形）：

```python
class OkxProvider(InterfaceProvider):
    provider = "okx"; market = "crypto"
    FIELD_SCHEMA: list[dict] = []     # 公开行情，0 密钥
    PARAMS_SCHEMA: list[dict] = []
```

**调用现有**：

- `_apply_exit_config(adapter)`（`engine.py:63`）——engine 构造 adapter 时注入代理；OKX 只要
  `supports_exit_config=True` 即自动纳入，**engine 侧零改动**。
- `CapabilityDecl(kind, temporality, markets, sub_kinds=…)`（`quant_common/contract.py`）。
- `proxies_map(dsn)`（`data_platform/proxy.py`）——requests `proxies` 的**唯一真源**。
- `_crypto_window(cfg, end_date, backfill_from, first_run_days)`——Crypto 窗口（T+1 上界＝UTC 昨日）。
- `_make_crypto_bar_handler(...)`——本批由 `_make_binance_bar_handler` **泛化**而成，两所共用。

## 验收标准（与实证结果）

```bash
cd server && venv/bin/python -m pytest tests/test_batch102b_okx.py -q     # ✅ 67 passed
cd server && venv/bin/python -m pytest tests/ -q                          # ✅ 2167 passed / 1 skipped（基线 2100）
cd server && venv/bin/python -m ruff check <改动文件…>                     # ✅ All checks passed!
bash scripts/test-migration-roundtrip.sh                                  # ✅ 用例 S/T/U/V 全绿（含全脚本其余用例）
cd server && venv/bin/alembic upgrade head                                # ✅ dev 真库到 0137（另跑过 downgrade 复核）
cd web && npm run prebuild                                                # ✅ 令牌门 PX39/HEX18/FS0/BTN21；词条门 1861 键对称
cd server && venv/bin/python -c "from src.data_platform.adapters.base import get_adapter; \
  a=get_adapter('okx'); print(a.provider, sorted(a.capabilities), a.supports_exit_config)"
  # → okx ['okx_perp_daily'] True
```

必测用例（全 mock，不连网/不连 OKX）——**均已覆盖**：`instruments` 过滤（只留 `settleCcy=USDT`）；
分页 `after` 游标推进（多页拼接 + 去重 + 排序 + 窗口外剔除 + 触顶告警）；**429/5xx/网络异常/`code=50011` 四路重试**
（含 `Retry-After` 优先、非限频码不重试）；滚动窗口节流；`confirm=0` 过滤（含缺列按已收盘）；
**张数坑**（断言 `volume==volCcy`、`amount==volQuote`）；日界（`bar=1Dutc` 出现在请求参数；ts 落 UTC）；
`code!='0'` 响亮抛；`to_bar_rows` 11 字段 + `.OKX` 后缀 + 不跨所归一 + 过写入门；三注册表完备 +
`_PROVIDER_MODULES` 含 okx/不含 okx_perp；`PROVIDER_MARKET`/`SYNC_ID_CAP_MAP` 已登记；`capable_consumers()` 含 okx；
`supports_exit_config` 真落到 requests.proxies；handler 游标/回补/失败可见/`label`；迁移 0136 结构与**零 start_floor 反臆造钉**。

**✅ 真机验证（上产后，prod · 2026-10-06 · 已执行，六步）**：
① `POST /api/proxies/aws-tokyo/probe` —— ⚠️ **未消**：补 PySocks 后不再 `InvalidSchema`，但经代理报
   `ConnectionError: Connection reset by peer`（**与数据面 7 标的失败同族**——出口侧压力下零星重置 ≈1.4%，
   非 probe 独有；已挂待办）；
② 配 `proxy_binding('okx', enabled=true, proxy_name='aws-tokyo')` —— ✅（`capable_consumers()==['binance','okx']`）；
③ 手动触发 `okx_perp_daily` → `bar_1d` 出现 `%.OKX` 行，与 `%.BINANCE` 行 **共存不混** —— ✅
   （**485 标的 / 13921 行 / 940s**，`status=partial`（7 标的失败，见下）；BTC/ETH/SOL 各 30 行，与
   `BTCUSDT.BINANCE` 同日共存；游标 `last_sync_date=20261005`）；
④ 抽样对账 + 量纲 —— ✅ **逐位一致**：`BTC-USDT-SWAP.OKX` 末根 `close=85715.0 vol=73622.5554` ⟷
   官方 `c=85715 / volCcy=73622.5554`；`ETH-USDT-SWAP.OKX` 末根 `close=2708.96 vol=1991154.761` ⟷
   官方 `c=2708.96 / volCcy=1991154.761`；`confirm=0` 的 2026-10-06（未收盘）被正确剔除；
⑤ 实测最老可得日期 → 回填 `sync_config.start_floor` —— ✅ 实测 **2020-01-01**
   （`instruments.listTime` **不是**数据下界）→ 迁移 **0137**；
⑥ 若 `1Dutc` 不被接受 → 改走 `1Hutc` 聚合 —— ✅ **被接受**（`1D` ⟷ `1Dutc` 的 ts 差 28800000ms＝正好 8h）
   ⇒ 不需 `1Hutc` 分支，**零多余代码**。

> 完整取证（含「**上产即暴露的运维事实**：0136 `enabled=true` ⇒ 部署后 beat 立刻自动触发一次、
> 此刻代理未配 ⇒ 直连超时 4×30s+退避＝134.3s ⇒ 响亮失败/游标不动」与 probe 残项定性）见设计 **§4.6**。

## mock 方式（实现后的真实打桩点）

```python
# 不连网：patch 实例方法 `_request`（返回源原生 JSON dict，形状对齐 OKX v5）
def fake_request(self, path, params):
    if path == "/api/v5/public/instruments":
        return {"code": "0", "data": [
            {"instId": "BTC-USDT-SWAP", "settleCcy": "USDT"},
            {"instId": "BTC-USD-SWAP",  "settleCcy": "BTC"},          # 币本位，须被过滤
        ]}
    if path == "/api/v5/market/history-candles":
        assert params["bar"] == "1Dutc"                              # 🔴 日界断言
        return {"code": "0", "data": [
            ["1791216000000", "100", "110", "90", "105", "10", "0.1", "10.5", "1"],  # confirm=1
            ["1791302400000", "105", "108", "95", "99",  "10", "0.1", "10.1", "0"],  # confirm=0 → 剔
        ]}
    return {"code": "0", "data": []}

with patch.object(OA.OkxAdapter, "_request", fake_request):
    a = OA.OkxAdapter()
    assert a.list_symbols(refresh=True) == ["BTC-USDT-SWAP"]
    rows = a.to_bar_rows(a.pull_daily("BTC-USDT-SWAP", "20260101", "20260102"), "1D")
    assert rows[0][0] == "BTC-USDT-SWAP.OKX" and rows[0][7] == 0.1      # volume = volCcy（不是 vol）
    assert len(rows) == 1                                               # confirm=0 已剔

# 重试面：patch `OA.requests.get` 用 side_effect 依次 429/503/ConnectionError/{"code":"50011"} 再成功，
#          并 patch `OA.time.sleep`（否则真睡 2/4/8s）；节流窗须让 time.monotonic 前进（否则 while 死循环）。
# 三注册表：直接读三处注册表断言 'okx' 在集合里（防「清单有·实现无」）。
```

> ⚠️ 打桩后若触及 60s 进程缓存（`load_role_permissions`/`load_registry`）须 `saved=dict(...)` + try/finally 归还；
> OKX adapter 的 `list_symbols`/`_req_times` 都是**实例级**（每任务新建 ⇒ 无跨用例污染）。
