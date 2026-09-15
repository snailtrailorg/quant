"""批9 守门测试：data_platform 懒导出不变量 + beat 链纯净性。

背景（2026-09-08 双盲审 A/B 同判 P1）：包属性 platform / 子模块 platform.py /
单例实例三层同名是占用 import 机制的保留地（import 子模块会自动 setattr 包属性），
eager 时代靠固定 import 顺序掩盖，lazy 化后暴露「投毒」绑定竞态。根治=模块改名
_platform.py（名字不撞）。本文件固化不变量，防回潮：
- 任何顺序下 `from src.data_platform import platform` 必须拿到 DataPlatform 单例
- beat/调度入口 import 链必须无 pandas/numpy（批9 C 子批核心目标）
"""
import subprocess
import sys
import types
from pathlib import Path

import src.data_platform as dp

_SERVER_ROOT = Path(__file__).resolve().parents[1]   # server/（src 包与 .env 所在）


def test_platform_attr_is_instance_not_module():
    """包属性 platform 必须是 DataPlatform 单例，绝不是模块（投毒的病灶形态）。"""
    from src.data_platform import DataPlatform, platform
    assert isinstance(platform, DataPlatform)
    assert not isinstance(platform, types.ModuleType)
    assert dp.platform is platform
    assert hasattr(platform, "is_trading_day") and hasattr(platform, "ensure_daily")


def test_cross_name_resolution_order():
    """盲审A 跨名投毒路径：先解析类、再解析实例，顺序不影响结果（子进程隔离首访顺序）。"""
    code = (
        "from src.data_platform import DataPlatform\n"
        "from src.data_platform import platform\n"
        "import types\n"
        "assert not isinstance(platform, types.ModuleType), '跨名投毒回潮'\n"
        "assert hasattr(platform, 'is_trading_day')\n"
    )
    subprocess.run([sys.executable, "-c", code], cwd=_SERVER_ROOT, check=True)


def test_beat_import_chain_heavy_free():
    """beat/调度入口 import 链无 pandas/numpy（批9 内存治理核心不变量）。

    批26-11 升级（哨兵法）：因子 exec 回染已根治——load_factors_from_db 从 app.py 模块
    import 期挪进 worker_process_init 信号。哨兵替换 factor.load_factors_from_db 后
    import app，断言哨兵零调用（DB 有无自定义因子行都与断言无关——import 期必不触碰）。
    """
    code = (
        "import sys\n"
        "import src.strategy_framework.factor as F\n"
        "calls = []\n"
        "F.load_factors_from_db = lambda: (calls.append(1), [])[1]   # 哨兵（app.py:90 from-import 绑定生效）\n"
        "import src.scheduler.app\n"
        "assert not calls, 'import 期仍触发因子加载（批26-11 回归——应只在 worker_process_init）'\n"
        "assert 'pandas' not in sys.modules, 'beat 链被 pandas 污染'\n"
        "assert 'numpy' not in sys.modules, 'beat 链被 numpy 污染'\n"
    )
    subprocess.run([sys.executable, "-c", code], cwd=_SERVER_ROOT, check=True)
