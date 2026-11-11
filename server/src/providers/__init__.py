"""D32 provider 插件包根（batch 172a）——本批只建空架。

新增一个 provider 的完整步骤（D32 §9.6 可插拔定义）：
1. 新建 `server/src/providers/<id>/`（manifest.py + plugin.py + _transport.py）
2. 在 `providers/__init__.py` 加一行 `from . import <id>  # noqa: F401` 触发注册
3. 配置里加一行账号记录

核心目录（quant_common / ports / registry / strategy_runner / strategy_framework /
md_hub / web_api）零改动。

示例（P3 落地 XTP 时）：
    from . import xtp  # noqa: F401  —— import 即注册
"""

from __future__ import annotations

# 批次 172a：空架。P3/P4/P5 在此追加 import 触发注册。
__all__: list[str] = []
