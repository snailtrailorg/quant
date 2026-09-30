"""jsonb 写路径**唯一出口**（层 1：与 `db.py` 同层——jsonb 是**存储关切**）。

## 为什么需要这个模块

`json.dumps` 的产物经 `%s::jsonb` 落库时，如果调用方**已经序列化过一次**、又被 dumps
第二次（`json.dumps(json.dumps(x))`），PostgreSQL **不报任何错**，只会把 jsonb 对象
静默降级成 jsonb **字符串**：

    {"rate_limits": {"a": 1}}      <- 想要
    "{\\"rate_limits\\": {\\"a\\": 1}}"    <- 实际落库（jsonb_typeof = 'string'）

后果：写入端全绿、读端若带 `json.loads` 兜底还会「恰好治愈」，只有「在 SQL 里
`col->>'k'` 取字段」的消费方悄悄拿到 NULL。报错点离病根极远——2026-09-30 在
`data_source`/`trading_account` 实测到两行真实损坏（迁移 0120 修复）。

**驱动包装本身不防这个坑**（实测 psycopg 3.3.4）：`Jsonb(json.dumps({"a": 1}))`
同样产出 `jsonb_typeof='string'`。所以守卫必须在包装**之内**——契约看**类型**
不看内容：`str`/`bytes` 一律响亮拒绝（连能过 `json.loads` 的 `"null"` 串也拒）。

## 为什么放 `data_platform` 而不是 `quant_common`

第一版落在 `quant_common/jsonb.py`，被分层闸
`tests/test_layering.py::test_quant_common_third_party_whitelist` 当场拦下：层 0 底座只许
stdlib + 显式白名单（cryptography/dotenv/redis），而本模块必须 import **psycopg**。
**闸门拦对了**——「写 jsonb 列」是存储关切，本就属拥有 DB 的层 1，不该塞进纯底座；
扩白名单会让层 0 从此知道 PostgreSQL 的存在。全仓调用方（web_api / data_sync /
strategy_framework / im_bot / feishu_bot）本已依赖 `data_platform`，零新增依赖边。

## 两条线（线表示不同、契约律相同）

- `jsonb(v)`：Python 对象 → 可直接绑参的驱动级 jsonb 参数（`psycopg.types.json.Jsonb`）。
  用于 SQL 直接绑参的写点（`VALUES (%s)` / `SET col=%s` / `col=%s::jsonb` 皆可，
  后者是无副作用的同型 cast）。
- `dumps_jsonb(v)`：Python 对象 → 已序列化 JSON 文本。保留给「文本经 `%s::jsonb`
  转型」的既有惯用法（如 `config_store._param_jsonb`）。

两者共用同一处 `json.dumps`：本模块是全仓 jsonb 载荷序列化的单点。
"""

from __future__ import annotations

import json
from typing import Any

from psycopg.types.json import Jsonb

__all__ = ["jsonb", "dumps_jsonb", "Jsonb"]


def _dumps(o: Any) -> str:
    """唯一序列化实现（`ensure_ascii=False` 保中文可读；与既有各写点逐字一致）。

    刻意**不**改 `allow_nan`——沿用 `json.dumps` 默认（NaN → `NaN` 字面量，PG jsonb
    拒收）。NaN 的防线在各自调用方（如 `strategy._log_signal_order` 的 `ok_fp`），
    本模块不改变既有语义。
    """
    return json.dumps(o, ensure_ascii=False)


#: 暴露序列化实现本身，便于测试与「单出口」断言（勿在别处再写 json.dumps 落 jsonb）。
JSONB_DUMPS = _dumps


def _reject_serialized(v: Any) -> None:
    if isinstance(v, (str, bytes, bytearray)):
        head = v[:40] if isinstance(v, str) else bytes(v[:40])
        raise TypeError(
            "jsonb 载荷入参须为 Python 对象（dict/list）；序列化由 data_platform.jsonb "
            f"单出口完成，收到已序列化字符串 {head!r}——疑似双重编码"
            "（json.dumps(json.dumps(x)) 不报错，只会把对象静默降级成 jsonb 字符串）")


def jsonb(v: Any):
    """Python 对象 → 驱动级 jsonb 参数；`None` → `None`（SQL NULL，非 jsonb null）。

    拒 `str`/`bytes`：那正是双重编码的产物形态。
    """
    if v is None:
        return None
    _reject_serialized(v)
    return Jsonb(v, dumps=_dumps)


def dumps_jsonb(v: Any) -> str | None:
    """Python 对象 → 已序列化 JSON 文本；`None` → `None`（SQL NULL）。"""
    if v is None:
        return None
    _reject_serialized(v)
    return _dumps(v)
