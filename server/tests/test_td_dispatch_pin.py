"""批 67：main 分发直接钉（批 65 挂账——D26-B 收官解锁；防 builder 回流 re-export 变真身）。"""
import ast
from pathlib import Path


def test_main_no_local_td_builders():
    """AST def 扫：main.py 源码不得出现 def _build_*_runtime（本地 builder 复活防线——
    def 扫天然放过 import/合法别名 _build_xtp_setting〔非 runtime 族〕）。"""
    src = Path("src/strategy_runner/main.py").read_text()
    tree = ast.parse(src)
    defs = [n.name for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]
    bad = [d for d in defs if d.startswith("_build_") and d.endswith("_runtime")]
    assert not bad, f"本地 TD builder 复活: {bad}（应住 td_registry）"


def test_dispatch_is_registry_function():
    """分发入口 is 同一函数（防本地重定义遮蔽 td_registry 版）。"""
    import src.strategy_runner.main as main
    import src.strategy_runner.td_registry as reg
    assert main.build_td_runtime is reg.build_td_runtime


def test_run_hub_mode_calls_registry():
    """_run_hub_mode 源含调用点（分发路径在位）。"""
    import inspect
    import src.strategy_runner.main as main
    assert "build_td_runtime(" in inspect.getsource(main._run_hub_mode)
