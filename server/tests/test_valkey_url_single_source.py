"""守门测试：Valkey 接入单源冻结（批 81 P2-5 收敛，2026-09-30）。

背景：VALKEY_URL 默认连接串曾拷贝 34 处（审计 2026-09-29 单一真源扫描 D 段）——
改默认值要动 N 处，漏一处即静默连错实例（P0-3 飞书池隔离失效即已发生后果）。
现已收敛到两层单源：`quant_common/config.py`（URL）+ `quant_common/redis_client.py`（client）。

本测试四锁：
1. 取数单源——白名单外禁止 `environ.get("VALKEY_URL"/"FEISHU_VALKEY_URL")` 字面量；
2. **client 单源——白名单外禁止任何 redis `from_url(` 直调**（不论首参是字面量、
   单源函数调用还是变量——「先算值再传」的间接写法曾是守门盲区，2026-09-30 收紧）；
3. 配置串单源——白名单外禁止 `redis://` 字符串常量（防换名拷贝）；
4. 语义钉——各读各变量、未配置落各自默认库、FEISHU 不跟随 VALKEY_URL（P0-3）、
   空串按配置错误原样返回不回落默认实例。
"""
import ast
import os
import re
from pathlib import Path

_SERVER_ROOT = Path(__file__).resolve().parents[1]
_SCAN_DIRS = ["src", "scripts"]

# 白名单：单源本体 + client 工厂 + web 共享池本体 + 冒烟脚本的故意覆盖
# （run_worker_smoke 置拒绝端口做 halt-edge 兜底）
ENV_ALLOWLIST = {
    "src/quant_common/config.py",
    "scripts/run_worker_smoke.py",
}
FROM_URL_ALLOWLIST = {
    "src/quant_common/redis_client.py",   # 工厂本体：全仓唯一允许直调 from_url 的地方
    "src/web_api/redis_pool.py",          # web 进程共享池本体（批27-2 ConnectionPool 语义）
}
URL_LITERAL_ALLOWLIST = {
    "src/quant_common/config.py",         # 默认串真源
    "scripts/run_worker_smoke.py",
}

ENV_GET_RE = re.compile(r'environ\.get\(\s*"(?:VALKEY_URL|FEISHU_VALKEY_URL)"')
_REDIS_WORDS = {"Redis", "StrictRedis", "ConnectionPool"}


def _iter_py_files():
    for d in _SCAN_DIRS:
        for p in (_SERVER_ROOT / d).rglob("*.py"):
            yield p


def _strip_docstrings(tree: ast.AST) -> None:
    """把 docstring 常量节点摘掉（说明文字里会引用旧写法，非取数点）。"""
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Module)):
            if node.body and isinstance(node.body[0], ast.Expr) and \
                    isinstance(node.body[0].value, ast.Constant) and \
                    isinstance(node.body[0].value.value, str):
                node.body[0].value.value = ""


def _dotted(node: ast.AST) -> str:
    """拼出属性链文本：Name/Attribute → `redis.Redis` / `_redis` / `redis.ConnectionPool`。"""
    parts = []
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if isinstance(node, ast.Name):
        parts.append(node.id)
    return ".".join(reversed(parts)) if parts else ""


def _is_redis_from_url(call: ast.AST) -> bool:
    """`<redis 系接收者>.from_url(...)`——接收者链含 redis 字样，或末段是 Redis/
    StrictRedis/ConnectionPool（兼容 `import redis as r` 这类改名）。"""
    if not (isinstance(call, ast.Call)
            and isinstance(call.func, ast.Attribute)
            and call.func.attr == "from_url"):
        return False
    chain = _dotted(call.func.value)
    if not chain:
        return False
    segs = chain.split(".")
    return any("redis" in s.lower() for s in segs) or segs[-1] in _REDIS_WORDS


def _from_url_lines(src: str) -> list[int]:
    tree = ast.parse(src)
    _strip_docstrings(tree)
    return [n.lineno for n in ast.walk(tree) if _is_redis_from_url(n)]


def test_no_env_get_valkey_url_literals():
    """除白名单外，禁止任何 environ.get(VALKEY_URL/FEISHU_VALKEY_URL) 取数（默认串拷贝或裸取）。"""
    offenders = []
    for p in _iter_py_files():
        rel = p.relative_to(_SERVER_ROOT).as_posix()
        if rel in ENV_ALLOWLIST:
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


def test_no_direct_redis_from_url_outside_factory():
    """client 一律走 quant_common.redis_client 的 business_redis()/feishu_redis()——
    白名单外**任何** redis from_url( 直调都算散落，不论首参形态。

    收紧理由（2026-09-30 专家复核）：旧规则只匹配首参为 `valkey_url()` 字面调用，
    `U = feishu_valkey_url(); redis.Redis.from_url(U, ...)` 这类「先算值再传」的
    间接写法整套绕过守门（feishu_bot/tasks.py 即此形态）。按接收者结构判禁，不再猜参数。
    """
    offenders = []
    for p in _iter_py_files():
        rel = p.relative_to(_SERVER_ROOT).as_posix()
        if rel in FROM_URL_ALLOWLIST:
            continue
        src = p.read_text(encoding="utf-8")
        if "from_url" not in src:
            continue
        try:
            lines = _from_url_lines(src)
        except SyntaxError:
            offenders.append(rel)  # 解析不了按命中处理（人工看）
            continue
        offenders += [f"{rel}:line{ln}" for ln in lines]
    assert not offenders, (
        "Valkey client 须走 quant_common.redis_client.business_redis()（db0）/"
        "feishu_redis()（db4，P0-3 隔离）——白名单外禁止任何 redis from_url 直调"
        f"（含先算值再传的间接写法）：{offenders}")


def test_no_redis_url_literals_outside_config():
    """配置串单源：白名单外禁止 `redis://` 字符串常量（防「换个名字再拷一份」）。"""
    offenders = []
    for p in _iter_py_files():
        rel = p.relative_to(_SERVER_ROOT).as_posix()
        if rel in URL_LITERAL_ALLOWLIST:
            continue
        src = p.read_text(encoding="utf-8")
        if "redis://" not in src:
            continue
        try:
            tree = ast.parse(src)
        except SyntaxError:
            offenders.append(rel)
            continue
        _strip_docstrings(tree)
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, str) \
                    and "redis://" in node.value:
                offenders.append(f"{rel}:line{node.lineno}")
    assert not offenders, (
        "redis:// 连接串常量只许出现在 quant_common/config.py（单源默认值）；"
        f"其余位置请用 valkey_url()/feishu_valkey_url()：{offenders}")


def test_env_templates_declare_feishu_url():
    """装机模板必须显式声明 FEISHU_VALKEY_URL 且与业务库不同号。

    2026-09-30 补：`.env.example`/`init-env.sh` 原只给 VALKEY_URL=db4、不给 FEISHU
    ——照模板装机时飞书侧只能吃代码默认 db4，与业务库同号，P0-3 隔离在该拓扑下静默失效
    （审计 2026-09-29 已记「谁照示例部署谁出事」，属未闭环项）。
    """
    texts = {
        ".env.example": (_SERVER_ROOT / ".env.example").read_text(encoding="utf-8"),
        "scripts/init-env.sh": (_SERVER_ROOT / "scripts" / "init-env.sh").read_text(encoding="utf-8"),
    }
    for name, text in texts.items():
        def _db(key: str) -> str | None:
            m = re.search(rf"^{key}=redis://[^:\s]+:\d+/(\d+)", text, re.M)
            return m.group(1) if m else None
        biz, feishu = _db("VALKEY_URL"), _db("FEISHU_VALKEY_URL")
        assert feishu is not None, f"{name} 缺 FEISHU_VALKEY_URL 显式声明（会吃代码默认）"
        assert biz is not None, f"{name} 缺 VALKEY_URL"
        assert feishu != biz, f"{name}: 飞书库与业务库同号（db{biz}）= 隔离失效（P0-3）"


def test_business_and_feishu_pools_use_different_db(monkeypatch):
    """行为级验收（技术债清单 P0-3 原定判据）：设了环境变量时，业务池与飞书池 db 号必须不同。"""
    from src.quant_common.redis_client import business_redis, feishu_redis
    monkeypatch.setenv("VALKEY_URL", "redis://127.0.0.1:6379/4")
    monkeypatch.setenv("FEISHU_VALKEY_URL", "redis://127.0.0.1:6379/7")
    b_db = business_redis().connection_pool.connection_kwargs["db"]
    f_db = feishu_redis().connection_pool.connection_kwargs["db"]
    assert b_db != f_db, f"业务库与飞书库同号（db{b_db}）= 隔离失效"


def test_gate_detects_indirect_from_url_pattern():
    """守门自检（防守门自身被削弱）：间接写法与直调写法都必须被识别，非 redis 的
    from_url 不得误伤。"""
    indirect = (
        "U = feishu_valkey_url()\n"
        "r = redis.Redis.from_url(U, decode_responses=True)\n"
    )
    direct = "r = redis.Redis.from_url(feishu_valkey_url())\n"
    pool = "p = redis.ConnectionPool.from_url(valkey_url())\n"
    aliased = "r = r2.Redis.from_url(U)\n"
    not_redis = "u = httpx.URL.from_url('http://x')\n"

    assert _from_url_lines(indirect) == [2], "间接写法（先算值再传）必须被识别"
    assert _from_url_lines(direct) == [1]
    assert _from_url_lines(pool) == [1]
    assert _from_url_lines(aliased) == [1], "接收者改名后仍须识别（Redis 末段判定）"
    assert _from_url_lines(not_redis) == [], "非 redis 的 from_url 不得误伤"


def test_valkey_url_semantics(monkeypatch):
    """语义钉：各读各变量、未配置落各自默认库；FEISHU 不跟随 VALKEY_URL（P0-3）；
    空串按配置错误原样返回（严格 .get，不回落默认实例）。"""
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
    # 空串 ≠ 未配置（不静默降级到 127.0.0.1：那是 P0-3 同类的「静默连错实例」）
    for k in ("VALKEY_URL", "FEISHU_VALKEY_URL"):
        monkeypatch.setenv(k, "")
    assert config.valkey_url() == ""
    assert config.feishu_valkey_url() == ""
