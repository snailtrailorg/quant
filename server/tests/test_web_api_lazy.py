"""批10 守门测试：web-api 启动链纯净性（pandas/numpy 零加载）。

批9 治 beat 链、批10 治 web-api 链：routes/stock.py 曾是 web-api 唯一模块级 pandas
载体（sys.meta_path spy 实证；+57MB RSS，全部用途=两个 pd.notna()）。删净后本文件
固化不变量防回潮——任何路由再加模块级重库 import 即红。重库按需路由（回测/分析/
convertible-terms）应保持函数级 import（用时付出，不用不驻留）。
"""
import subprocess
import sys
from pathlib import Path

_SERVER_ROOT = Path(__file__).resolve().parents[1]   # server/（src 包与 .env 所在）


def test_webapi_import_chain_heavy_free():
    """web-api 入口 import 链无 pandas/numpy（批10 内存清理核心不变量）。

    子进程隔离（同 tests/test_data_platform_lazy.py 模式）：避免本测试进程的
    pandas 状态串扰。conftest 已注入测试 JWT_SECRET，import 无 DB 副作用。
    """
    code = (
        "import sys\n"
        "import src.web_api.main\n"
        "assert 'pandas' not in sys.modules, 'web-api 链被 pandas 污染（回潮：某路由模块级重库 import？）'\n"
        "assert 'numpy' not in sys.modules, 'web-api 链被 numpy 污染'\n"
    )
    subprocess.run([sys.executable, "-c", code], cwd=_SERVER_ROOT, check=True)
