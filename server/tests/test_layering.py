"""模块分层断言（2026-08-19 模块归位 P 审交付物）：把架构理念变成测试。

规则：
1. quant_common 纯度：禁止 import 任何 src.* 业务模块（它被所有层依赖，反向=全图成环）；
   第三方白名单（cryptography/python-dotenv）之外不得引入重型依赖
2. 层级禁上行：LAYER 定义分层，下层不得 import 上层（lazy import 一并计入——本次 6 条违规
   全是函数内 lazy import，只扫模块级看不见）
3. 特定禁边（历史违规回归锁）：feishu_bot→web_api 等寄生曾两次发生
"""
import os
import re

SRC = os.path.join(os.path.dirname(__file__), "..", "src")

LAYER = {
    "quant_common": 0,
    "data_platform": 1, "alert_notify": 2, "task_manager": 2, "strategy_framework": 2,
    "risk_control": 2, "astock_analysis": 2, "llm_gateway": 2, "strategies": 2,
    "strategy_runner": 3, "md_hub": 3, "data_sync": 3, "scheduler": 3, "health_monitor": 3,
    "web_api": 4, "feishu_bot": 4,
    "email_service": 2,
}
# 合法的跨层豁免（横向服务依赖，已审）：
EXEMPT_UPWARD = {
    ("alert_notify", "data_platform"),      # 告警写 PG/Valkey（横向，data_sync 等同款）
    ("data_platform", "alert_notify"),      # tushare_adapter 同步失败告警（lazy）
    ("task_manager", "alert_notify"),
    ("health_monitor", "data_platform"),
    ("llm_gateway", "alert_notify"),
    ("strategy_runner", "alert_notify"),
    ("strategy_framework", "data_platform"),
}
FORBIDDEN_EDGES = {
    ("feishu_bot", "web_api"),   # P-F1：audit_log 曾寄生 web_api.auth（已下沉 data_platform）
    ("md_hub", "strategy_runner"),   # 共享工具曾寄生 runner（已归位 quant_common）
    ("scheduler", "web_api"),   # 业务逻辑曾寄生 HTTP 入口（已归位 llm_gateway/email_service）
}


def _module_edges():
    """提取模块级+函数内（lazy）的全部 src.* 依赖边。"""
    edges = set()
    for root, dirs, files in os.walk(SRC):
        dirs[:] = [d for d in dirs if d != "__pycache__"]
        for f in files:
            if not f.endswith(".py"):
                continue
            path = os.path.join(root, f)
            rel = os.path.relpath(path, SRC)
            parts = rel.split(os.sep)
            src_mod = parts[0][:-3] if parts[0].endswith(".py") else parts[0]
            txt = open(path, encoding="utf-8", errors="ignore").read()
            for m in re.finditer(r"(?:from|import)\s+src((?:\.\w+)*)", txt):
                segs = [s for s in m.group(1).split(".") if s]
                if segs and segs[0] != src_mod:
                    edges.add((src_mod, segs[0]))
    return edges


class TestLayering:
    edges = None

    @classmethod
    def setup_class(cls):
        cls.edges = _module_edges()

    def test_quant_common_purity(self):
        """quant_common 是公共底座：出边只许指向非 src 模块（业务反向=全图成环）。"""
        bad = [e for e in self.edges if e[0] == "quant_common" and e[1] not in EXEMPT_UPWARD
               and self._is_src(e[1])]
        assert not bad, f"quant_common 出现业务依赖: {bad}"

    def test_no_upward_imports(self):
        """下层不得 import 上层（lazy 计入）。"""
        violations = []
        for a, b in self.edges:
            la, lb = LAYER.get(a), LAYER.get(b)
            if la is not None and lb is not None and la < lb and (a, b) not in EXEMPT_UPWARD:
                violations.append(f"{a}(层{la}) → {b}(层{lb})")
        assert not violations, f"层级违规:\n  " + "\n  ".join(sorted(violations))

    def test_forbidden_edges_never_return(self):
        """历史寄生边回归锁（发生过的错位不许复发）。"""
        bad = [e for e in FORBIDDEN_EDGES if e in self.edges]
        assert not bad, f"历史违规边复发: {bad}"

    def test_quant_common_third_party_whitelist(self):
        """第三方白名单（P-S4：'只许 stdlib'自相矛盾——cryptography/dotenv 是基础库）。"""
        allowed = {"cryptography", "dotenv"}
        path = os.path.join(SRC, "quant_common")
        for f in os.listdir(path):
            if not f.endswith(".py"):
                continue
            txt = open(os.path.join(path, f), encoding="utf-8", errors="ignore").read()
            for m in re.finditer(r"^(?:from|import)\s+(\w+)", txt, re.M):
                top = m.group(1)
                if top in ("src", "from", "import", "__future__"):
                    continue
                if top not in allowed and not _is_stdlib(top):
                    raise AssertionError(f"quant_common/{f} 引入白名单外依赖: {top}")

    @staticmethod
    def _is_src(mod):
        return os.path.isdir(os.path.join(SRC, mod)) or \
            os.path.exists(os.path.join(SRC, mod + ".py"))


def _is_stdlib(name):
    import sys
    return name in getattr(sys, "stdlib_module_names", ()) or name in sys.builtin_module_names
