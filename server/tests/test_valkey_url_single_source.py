"""守门测试：VALKEY_URL 单源冻结（批 81 P2-5 收敛，2026-09-30）。

背景：VALKEY_URL 默认连接串曾拷贝 34 处（审计 2026-09-29 单一真源扫描 D 段）——
改默认值要动 N 处，漏一处即静默连错实例（P0-3 飞书池隔离失效即已发生后果）。
现已收敛到 quant_common/config.py 的 valkey_url()/feishu_valkey_url()。

本测试双锁：
1. 源码扫描——quant_common/config.py 与白名单外禁止 `environ.get("VALKEY_URL"`/
   `environ.get("FEISHU_VALKEY_URL"` 字面量取数（含默认串拷贝与无默认裸取）；
2. 语义钉——两个函数读各自环境变量、未配置时落各自默认 db（0/4），
   且 FEISHU 不跟随 VALKEY_URL（P0-3 隔离语义）。
"""
import ast
import os
import re
from pathlib import Path

_SERVER_ROOT = Path(__file__).resolve().parents[1]
_SCAN_DIRS = ["src", "scripts"]

# 白名单：单源本体 + 冒烟脚本的故意 env 覆盖（run_worker_smoke 置拒绝端口做 halt-edge 兜底）
ALLOWLIST = {
    "src/quant_common/config.py",
    "scripts/run_worker_smoke.py",
}

ENV_GET_RE = re.compile(r'environ\.get\(\s*"(?:VALKEY_URL|FEISHU_VALKEY_URL)"')


def _iter_py_files():
    for d in _SCAN_DIRS:
        for p in (_SERVER_ROOT / d).rglob("*.py"):
            yield p


def _strip_docstrings(tree: ast.AST) -> None:
    """把 docstring 常量节点摘掉（config.py 的说明文字里引用了旧写法，非取数点）。"""
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Module)):
            if node.body and isinstance(node.body[0], ast.Expr) and \
                    isinstance(node.body[0].value, ast.Constant) and \
                    isinstance(node.body[0].value.value, str):
                node.body[0].value.value = ""


def test_no_env_get_valkey_url_literals():
    """除白名单外，禁止任何 environ.get(VALKEY_URL/FEISHU_VALKEY_URL) 取数（默认串拷贝或裸取）。"""
    offenders = []
    for p in _iter_py_files():
        rel = p.relative_to(_SERVER_ROOT).as_posix()
        if rel in ALLOWLIST:
            continue
        src = p.read_text(encoding="utf-8")
        if not ENV_GET_RE.search(src):
            continue
        # 文本层命中后用 AST 复核（剥 docstring，避免注释/说明文字误伤）
        try:
            tree = ast.parse(src)
        except SyntaxError:
            offenders.append(rel)  # 解析不了按命中处理（人工看）
            continue
        _strip_docstrings(tree)
        for node in ast.walk(tree):
            if (isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Attribute)
                    and node.func.attr == "get"
                    and isinstance(node.func.value, ast.Attribute)
                    and node.func.value.attr == "environ"
                    and node.args
                    and isinstance(node.args[0], ast.Constant)
                    and node.args[0].value in ("VALKEY_URL", "FEISHU_VALKEY_URL")):
                offenders.append(f"{rel}:line{node.lineno}")
                break
    assert not offenders, (
        "VALKEY_URL/FEISHU_VALKEY_URL 取数必须走 quant_common.config.valkey_url()/"
        "feishu_valkey_url() 单源，禁止 environ.get 字面量："
        f"{offenders}")


def test_no_scattered_from_url_on_valkey_url():
    """业务库 client 一律走 quant_common.redis_client.business_redis()——
    禁止 `from_url(valkey_url(...))` 再散落（P2-5 收敛后补的 client 层守门；
    白名单=单源工厂自身 + web_api/redis_pool（web 共享池本体，批27-2 语义）。"""
    offenders = []
    for p in _iter_py_files():
        rel = p.relative_to(_SERVER_ROOT).as_posix()
        if rel in {"src/quant_common/redis_client.py", "src/web_api/redis_pool.py"}:
            continue
        src = p.read_text(encoding="utf-8")
        # 文本层初筛 + 剥 docstring 后 AST 复核：from_url 的首个位置参数是 valkey_url()/feishu_valkey_url() 调用
        if "from_url" not in src or "valkey_url" not in src:
            continue
        try:
            tree = ast.parse(src)
        except SyntaxError:
            offenders.append(rel)
            continue
        _strip_docstrings(tree)
        for node in ast.walk(tree):
            if (isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Attribute)
                    and node.func.attr == "from_url"
                    and node.args
                    and isinstance(node.args[0], ast.Call)
                    and isinstance(node.args[0].func, ast.Name)
                    and node.args[0].func.id in ("valkey_url", "feishu_valkey_url")):
                offenders.append(f"{rel}:line{node.lineno}")
                break
    assert not offenders, (
        "业务库 client 须走 quant_common.redis_client.business_redis()（池按参数共享），"
        f"禁止散落 from_url(valkey_url(...))：{offenders}")


def test_valkey_url_semantics(monkeypatch):
    """语义钉：各读各变量、未配置落各自默认库；FEISHU 不跟随 VALKEY_URL（P0-3）。"""
    from src.quant_common import config
    for k in ("VALKEY_URL", "FEISHU_VALKEY_URL"):
        monkeypatch.delenv(k, raising=False)
    assert config.valkey_url() == "redis://127.0.0.1:6379/0"
    assert config.feishu_valkey_url() == "redis://127.0.0.1:6379/4"
    monkeypatch.setenv("VALKEY_URL", "redis://10.0.0.9:6379/0")
    assert config.valkey_url() == "redis://10.0.0.9:6379/0"
    assert config.feishu_valkey_url() == "redis://127.0.0.1:6379/4"  # 不跟随
    monkeypatch.setenv("FEISHU_VALKEY_URL", "redis://10.0.0.9:6379/4")
    assert config.feishu_valkey_url() == "redis://10.0.0.9:6379/4"
