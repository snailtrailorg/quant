"""批 102a：代理配置体系（全局池 + 按消费方绑定）——scheme 词表 / 解析三态 / 出口注入。

覆盖三件最容易漂移的事：
1. **DSN scheme 词表＝{socks5h, socks5, http, https}**（威廉姆 2026-10-06 裁定不锁死 SOCKS），
   且**真正的硬约束是 DNS 解析侧**（`socks5h`/`http(s)` 代理侧、`socks5` 本地）——不是 scheme 名字。
2. **`resolve_proxy` 三态**与「**禁进程级缓存**」（无模块级缓存）。
3. **出口真的接上了**：解析点**只在 engine**（`_apply_exit_config`，构造 adapter 时一次，
   注入 `configure_exit`）；adapter 的 `exit_config()` 只读注入结果**不触库**——使直接手搓
   adapter 的单测（`_pull`/`list_symbols`）与 `test_engine_provider` 类**零 DB 耦合**。
   `_get` 把 proxy 传给 requests（`http(s)` 原生 / `socks*` 经 PySocks）、端点覆盖只作用于
   K 线下载基址、**SDK 型 adapter 不触库**且不可配（写入侧响亮拒绝）。

离线为默认（无 dev 库、无网）：DB 面一律打桩 `_load_binding`/`_load_endpoint`/`get_conn`。
"""
from __future__ import annotations

import importlib
from unittest.mock import MagicMock, patch

import pytest

from src.data_platform import proxy as P
from src.data_platform.adapters import binance_adapter as BA
from src.web_api.errors import ApiError

# ---------------------------------------------------------------------------
# 1：DSN 校验与掩码
# ---------------------------------------------------------------------------


class TestValidateUrl:
    @pytest.mark.parametrize("url", [
        "socks5h://host:1080",
        "socks5://host:1080",
        "http://host:3128",
        "https://user:pw@host:8443",
    ])
    def test_allowed_schemes(self, url):
        assert P.validate_url(url) == url

    @pytest.mark.parametrize("url", [
        "ftp://host:21",          # 词表外
        "host:1080",              # 无 scheme
        "http://host",            # 无端口（默认端口在不同代理实现上不一致，宁可写明确）
        "socks5h://",             # 无 host
        "",
        "   ",
    ])
    def test_rejected(self, url):
        with pytest.raises(ValueError):
            P.validate_url(url)

    def test_socks5_not_banned(self):
        """`socks5://`（本地 DNS）**放行**——不在 DDL/校验里武断封杀一个合法个例。"""
        assert P.validate_url("socks5://10.0.0.1:1080") == "socks5://10.0.0.1:1080"

    def test_local_dns_flag(self):
        assert P.is_local_dns("socks5://h:1") is True
        assert P.is_local_dns("socks5h://h:1") is False
        assert P.is_local_dns("http://h:1") is False
        assert P.is_local_dns("https://h:1") is False


class TestMask:
    @pytest.mark.parametrize("raw,expect", [
        ("socks5h://u:p@h:1080", "socks5h://***@h:1080"),
        ("http://u:p@h:3128", "http://***@h:3128"),
        ("https://tok@h:8443", "https://***@h:8443"),
        ("socks5h://h:1080", "socks5h://h:1080"),      # 无 userinfo → 原样
        ("http://h:3128", "http://h:3128"),
    ])
    def test_only_userinfo_masked(self, raw, expect):
        assert P.mask_url(raw) == expect


class TestValidateEndpoint:
    def test_ok_and_empty(self):
        assert P.validate_endpoint("https://mirror.example") == "https://mirror.example"
        assert P.validate_endpoint("") == ""
        assert P.validate_endpoint(None) == ""

    @pytest.mark.parametrize("ep", ["ftp://h", "mirror.example", "socks5h://h:1"])
    def test_rejected(self, ep):
        with pytest.raises(ValueError):
            P.validate_endpoint(ep)


# ---------------------------------------------------------------------------
# 2：解析三态 + 禁缓存
# ---------------------------------------------------------------------------


class TestResolveProxy:
    @pytest.mark.parametrize("binding,expect", [
        (None, None),                                       # 无绑定行
        ((False, "aws", "http://h:1", True), None),          # 绑定存在但未启用
        ((True, None, None, None), None),                    # 启用但没选（CHECK 本应拦住）
        ((True, "aws", None, None), None),                   # 悬空：池中无该行
        ((True, "aws", "http://h:1", False), None),          # 池内该行被停用
        ((True, "aws", "http://h:1", True), "http://h:1"),   # 正常
    ])
    def test_states(self, binding, expect):
        with patch("src.data_platform.proxy._load_binding", return_value=binding):
            assert P.resolve_proxy("binance") == expect

    def test_empty_consumer_short_circuits(self):
        with patch("src.data_platform.proxy._load_binding",
                   side_effect=AssertionError("空 consumer 不应触库")):
            assert P.resolve_proxy("") is None

    def test_no_module_level_cache(self):
        """**禁进程级缓存**：连续两次调用必须都读库（否则测试打桩 DB 会污染后续用例）。"""
        with patch("src.data_platform.proxy._load_binding",
                   return_value=(True, "aws", "http://h:1", True)) as lb:
            P.resolve_proxy("binance")
            P.resolve_proxy("binance")
        assert lb.call_count == 2


class TestResolveEndpoint:
    def test_reads_override(self):
        with patch("src.data_platform.proxy._load_endpoint", return_value="https://m"):
            assert P.resolve_endpoint("binance") == "https://m"

    def test_none_when_absent(self):
        with patch("src.data_platform.proxy._load_endpoint", return_value=None):
            assert P.resolve_endpoint("binance") is None
        assert P.resolve_endpoint("") is None


class TestBindingWrite:
    def test_enabled_without_name_rejected(self):
        with pytest.raises(ValueError):
            P.set_binding("binance", True, None)

    def test_endpoint_validated(self):
        with pytest.raises(ValueError):
            P.set_binding("binance", False, None, "ftp://x")

    def test_writes_and_commits(self):
        conn = MagicMock()
        conn.__enter__.return_value = conn
        with patch("src.data_platform.proxy.get_conn", return_value=conn):
            P.set_binding("binance", True, "aws", "https://m")
        assert conn.execute.called and conn.commit.called

    def test_upsert_validates_url_first(self):
        with pytest.raises(ValueError):
            P.upsert_proxy("aws", "ftp://h:1")

    def test_upsert_empty_url_creates_nothing(self):
        """新建行缺 url ⇒ **响亮拒绝**（不能建一条没有出口的代理）。"""
        conn = MagicMock()
        conn.__enter__.return_value = conn
        cur = MagicMock()
        cur.fetchone.return_value = None            # 该 name 不存在
        conn.execute.return_value = cur
        with patch("src.data_platform.proxy.get_conn", return_value=conn):
            with pytest.raises(ValueError):
                P.upsert_proxy("aws", "")
        assert not conn.commit.called                # 拒绝在写之前

    def test_upsert_empty_url_keeps_existing_url(self):
        """**留空不改**：url 空 ⇒ 只更新 notes/enabled，**绝不把掩码值回写**（毁凭证）。"""
        conn = MagicMock()
        conn.__enter__.return_value = conn
        cur = MagicMock()
        cur.fetchone.return_value = (1,)            # 已存在
        conn.execute.return_value = cur
        with patch("src.data_platform.proxy.get_conn", return_value=conn):
            P.upsert_proxy("aws", "", notes="n", enabled=False)
        sqls = [c.args[0] for c in conn.execute.call_args_list]
        assert any(s.startswith("UPDATE proxy_config") for s in sqls)
        assert not any("url=EXCLUDED.url" in s for s in sqls), "空 url 不得走 upsert 覆盖分支"
        assert conn.commit.called


class TestCapableConsumers:
    def test_binance_capable_tushare_not(self):
        capable = P.capable_consumers()
        assert "binance" in capable
        assert "tushare" not in capable          # SDK 型：未接线 ⇒ 不可配（写入侧会拒）
        assert "joinquant" not in capable


# ---------------------------------------------------------------------------
# 3：出口真的接上了（adapter 侧）
# ---------------------------------------------------------------------------


class TestAdapterExitConfig:
    def test_flags(self):
        assert BA.BinanceAdapter.supports_exit_config is True
        from src.data_platform.adapters.base import TushareAdapter
        assert TushareAdapter.supports_exit_config is False

    def test_injection_is_pure_no_db(self):
        """`configure_exit`/`exit_config` **不触库**——解析在 engine 侧；注入即读回。"""
        a = BA.BinanceAdapter()
        assert a.exit_config() == (None, None)                  # 未注入＝直连
        a.configure_exit(proxy="socks5h://h:1080")
        assert a.exit_config() == ("socks5h://h:1080", None)
        a.configure_exit(proxy=None, endpoint="https://mirror.example")
        assert a.exit_config() == (None, "https://mirror.example")

    def test_non_capable_adapter_rejects_proxy_injection(self):
        """SDK 型 adapter 被注入代理＝**响亮拒绝**（`UnsupportedFeature`），不静默绕过。"""
        from src.data_platform.adapters.base import TushareAdapter, UnsupportedFeature
        with pytest.raises(UnsupportedFeature):
            TushareAdapter.__new__(TushareAdapter).configure_exit(proxy="http://h:3128")

    def test_engine_resolves_and_injects_for_capable(self):
        """engine `_apply_exit_config`＝**唯一解析点**：capable adapter → 解析并注入，恰一次。"""
        from src.data_sync import engine as E
        a = BA.BinanceAdapter()
        with patch("src.data_platform.proxy.resolve_proxy",
                   return_value="socks5h://h:1080") as rp, \
             patch("src.data_platform.proxy.resolve_endpoint", return_value=None) as re_:
            out = E._apply_exit_config(a)
        assert out is a and a.exit_config() == ("socks5h://h:1080", None)
        assert rp.call_count == 1 and re_.call_count == 1

    def test_engine_non_capable_never_touches_db(self):
        """非 capable adapter **零 DB 耦合**（`test_engine_provider` 类单测得保）。"""
        class _Bare(BA.BaseDataAdapter):
            provider = "bare"

            def pull_daily(self, *a, **k):
                return None

            def pull_minute(self, *a, **k):
                return None

            def to_bar_rows(self, *a, **k):
                return []

        from src.data_sync import engine as E
        with patch("src.data_platform.proxy.resolve_proxy",
                   side_effect=AssertionError("supports_exit_config=False 不应触库")) as rp:
            out = E._apply_exit_config(_Bare())
        assert out.exit_config() == (None, None) and rp.call_count == 0

    def test_engine_falls_back_to_direct_on_resolution_error(self):
        """解析抛错（表缺/DB 抖动）⇒ **回落直连**且**不抛**——可选配置不该打挂同步任务。

        口径同 `_routing_pilot_on`（配置表读不到＝回退现状路径）；代价是「代理不生效」与
        「正常直连」表象同形 ⇒ 靠本条 WARNING + probe 端点兜底（见 `_apply_exit_config` docstring）。
        """
        from src.data_sync import engine as E
        a = BA.BinanceAdapter()
        with patch("src.data_platform.proxy.resolve_proxy",
                   side_effect=RuntimeError('relation "proxy_binding" does not exist')):
            out = E._apply_exit_config(a)              # 不抛
        assert out is a and a.exit_config() == (None, None)

    def test_exit_returns_base_override(self):
        a = BA.BinanceAdapter()
        a.configure_exit(proxy=None, endpoint="https://mirror.example")
        assert a._exit() == (None, "https://mirror.example")
        b = BA.BinanceAdapter()
        assert b._exit() == (None, BA._BATCH)              # 缺省＝代码常量


class TestHttpWiring:
    def test_proxies_map(self):
        assert BA._proxies(None) is None
        assert BA._proxies("http://h:3128") == {"http": "http://h:3128", "https": "http://h:3128"}
        assert BA._proxies("socks5h://h:1080") == {"http": "socks5h://h:1080",
                                                   "https": "socks5h://h:1080"}

    def test_list_symbols_passes_proxy_but_keeps_s3_endpoint(self):
        """符号枚举走 S3 固定入口 ⇒ **不随端点覆盖**；但代理照样生效。"""
        seen: dict = {}

        def fake_get(url, timeout=BA._TIMEOUT, proxy=None):
            seen["url"], seen["proxy"] = url, proxy
            return b"<ListBucketResult></ListBucketResult>"

        a = BA.BinanceAdapter()
        a.configure_exit(proxy="http://p:3128", endpoint="https://mirror.example")
        with patch.object(BA, "_get", fake_get):
            a.list_symbols(refresh=True)
        assert seen["proxy"] == "http://p:3128"
        assert seen["url"].startswith(BA._S3), seen["url"]

    def test_pull_uses_endpoint_override_base_and_proxy(self):
        seen: list = []

        def fake_get(url, timeout=BA._TIMEOUT, proxy=None):
            seen.append((url, proxy))
            return None                                    # 全 404 → 空帧，URL 已留证

        a = BA.BinanceAdapter()
        a.configure_exit(proxy="socks5h://p:1080", endpoint="https://mirror.example")
        with patch.object(BA, "_get", fake_get):
            a.pull_minute("BTCUSDT", "15min", "20261003", "20261004")
        assert seen, "未发起任何下载"
        assert all(u.startswith("https://mirror.example/data/futures/um/") for u, _ in seen), seen
        assert all(px == "socks5h://p:1080" for _, px in seen), seen


# ---------------------------------------------------------------------------
# 4：路由层（守卫 + 注册顺序 + 错误码）
# ---------------------------------------------------------------------------


class TestRouteLayer:
    def test_capable_guard_rejects_unwired_consumer(self):
        from src.web_api.routes import proxies as R
        with pytest.raises(ApiError) as ei:
            R._assert_consumer_capable("tushare")
        assert ei.value.code == "CONSUMER_NOT_PROXYABLE"

    def test_capable_guard_allows_binance(self):
        from src.web_api.routes import proxies as R
        R._assert_consumer_capable("binance")             # 不抛

    def test_upsert_requires_name(self):
        from src.web_api.routes import proxies as R
        with pytest.raises(ApiError) as ei:
            R.upsert_proxy({"name": "   "}, payload={"username": "u"})
        assert ei.value.code == "PARAM_INVALID"

    def test_probe_target_must_be_str(self):
        from src.web_api.routes import proxies as R
        with pytest.raises(ApiError) as ei:
            R.probe_proxy("aws", {"target": 5}, payload={"username": "u"})
        assert ei.value.code == "PARAM_INVALID"

    def test_probe_returns_store_result_not_raise(self):
        from src.web_api.routes import proxies as R
        with patch("src.data_platform.proxy.probe",
                   return_value={"ok": False, "error": "boom"}) as pr:
            out = R.probe_proxy("aws", None, payload={"username": "u"})
        assert out == {"ok": False, "error": "boom"} and pr.called

    def test_write_endpoints_are_post_not_put(self):
        """PUT→POST 硬切（本仓已立法）：写端点不得出现 PUT。"""
        from src.web_api.routes.proxies import router
        methods = {m for r in router.routes for m in getattr(r, "methods", set())}
        assert "PUT" not in methods

    def test_bindings_registered_before_param_sibling(self):
        """**遮蔽防御**：`/api/proxies/bindings/{consumer}` 与 `/api/proxies/{name}/probe`
        同为四段路径（`bindings/probe` 会同时命中）⇒ 静态段必须先注册。"""
        from src.web_api.routes.proxies import router
        paths = [r.path for r in router.routes]
        assert paths.index("/api/proxies/bindings/{consumer}") < \
            paths.index("/api/proxies/{name}/probe")


# ---------------------------------------------------------------------------
# 5：迁移 0135（离线：DDL 文本 + 降级对称）
# ---------------------------------------------------------------------------


class TestMigration0135:
    @staticmethod
    def _mod():
        return importlib.import_module("migrations.versions.0135_proxy_config")

    def test_revision_chain(self):
        m = self._mod()
        assert m.revision == "0135"
        assert m.down_revision == "0134"

    def _run(self, fn) -> str:
        m = self._mod()
        seen: list[str] = []
        with patch.object(m, "op") as o:
            o.execute.side_effect = seen.append
            fn(m)
        return "\n".join(seen)

    def test_upgrade_creates_both_tables_with_check(self):
        blob = self._run(lambda m: m.upgrade())
        assert "CREATE TABLE IF NOT EXISTS proxy_config" in blob
        assert "CREATE TABLE IF NOT EXISTS proxy_binding" in blob
        assert "ck_proxy_binding_name" in blob
        assert "NOT proxy_enabled OR proxy_name IS NOT NULL" in blob
        assert "REFERENCES proxy_config(name) ON DELETE SET NULL" in blob

    def test_upgrade_is_idempotent_by_convention(self):
        """建表幂等（`IF NOT EXISTS`）＝本仓 16/17 建表迁移惯例——部署中断强制复跑不炸。"""
        blob = self._run(lambda m: m.upgrade())
        assert blob.count("CREATE TABLE IF NOT EXISTS") == 2
        assert "CREATE TABLE proxy" not in blob.replace("CREATE TABLE IF NOT EXISTS proxy", "")

    def test_downgrade_drops_binding_first(self):
        """FK 依赖 ⇒ 先删引用方（顺序即正确性）。"""
        blob = self._run(lambda m: m.downgrade())
        assert blob.index("DROP TABLE IF EXISTS proxy_binding") \
            < blob.index("DROP TABLE IF EXISTS proxy_config")

    def test_does_not_seed_proxy_addresses(self):
        """seed 原则：迁移**不 seed** 任何代理地址（DSN 属运维态，非代码态）。"""
        blob = self._run(lambda m: m.upgrade())
        assert "INSERT INTO proxy_config" not in blob
        assert "socks5" not in blob and "http://" not in blob
