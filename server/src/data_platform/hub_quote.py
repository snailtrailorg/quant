"""批 171s 件一/件三：平仓同源行情读取 + 平仓品种规则/重试配置读。

- `get_hub_tick(account_id, symbol)`：只读同源 hub tick（`hub:latest_tick:{account_id}:{vt}`），
  **永不返回 None**（签名 `-> dict`）。键缺/JSON 坏/Valkey 不可达 → 返回 `{"_unavailable": code}`
  且 `logger.warning` 留痕（171 纪律：never-throw 但 never-silent、返回值永不塌缩）。
  消费方凭 `_unavailable` 分辨「系统性 Valkey 全断（valkey_down）」vs「单标的暂无 tick
  （no_tick）」vs「坏数据（bad_json）」，三者路径不可混。
- `get_close_policy(provider, category=None)` / `get_retry_config()`：从 `system_config` 读
  件三配置属性；缺行 / DB 读失败 / 值非法 → 回落代码缺省并 `logger.warning`（fail-open 留痕，
  **不照抄 `md_session.py:160` 的裸 `except: return`**）。

分层：本模块在 `data_platform`（层 1），只依赖 `quant_common`（层 0）与同层 db/schema；
`strategy_framework.adapters`（层 2）经此封装读配置，不 import `web_api`。
"""

from __future__ import annotations

import json
import logging
import math

from src.quant_common.redis_client import business_redis

logger = logging.getLogger(__name__)


# ── 件一：同源行情读取 ──

def get_hub_tick(account_id: int, symbol: str) -> dict:
    """读同源 hub tick；成功返回行情 dict（last/pre_close/upper_limit/lower_limit/bid/ask/ts…）。

    失败返回 `{"_unavailable": code}`（code ∈ valkey_down / bad_json / no_tick）并 warning 留痕，
    never-throw。`account_id is None` 硬拒（防 `hub:latest_tick:None:*` 全仓 no_quote）——
    这是唯一允许抛出的形态（显式 ValueError，属调用方契约错误而非运行期降级）。
    """
    if account_id is None:
        raise ValueError("get_hub_tick: account_id 必填（防 hub:latest_tick:None:* 全仓 no_quote）")
    try:
        from src.data_platform.schema import to_vt_symbol
        key = f"hub:latest_tick:{account_id}:{to_vt_symbol(symbol)}"
    except Exception as e:
        logger.warning("get_hub_tick symbol=%r 归一失败 unusable=no_tick: %s", symbol, e)
        return {"_unavailable": "no_tick"}
    try:
        r = business_redis(decode_responses=True, socket_timeout=2)
        raw = r.get(key)
    except Exception as e:
        logger.warning("get_hub_tick key=%s unusable=valkey_down: %s", key, e)
        return {"_unavailable": "valkey_down"}
    if raw is None:
        logger.warning("get_hub_tick key=%s unusable=no_tick: 键缺失", key)
        return {"_unavailable": "no_tick"}
    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, TypeError, ValueError) as e:
        logger.warning("get_hub_tick key=%s unusable=bad_json: %s", key, e)
        return {"_unavailable": "bad_json"}
    if not isinstance(data, dict):
        logger.warning("get_hub_tick key=%s unusable=bad_json: 值非 dict (%s)",
                       key, type(data).__name__)
        return {"_unavailable": "bad_json"}
    return data


# ── 件三：品种规则 / 重试配置（读 system_config，fail-open 留痕）──

# 返回键 -> 类型
_CLOSE_POLICY_TYPES = {
    "has_limit_band": "bool",
    "opponent_depth": "int",
    "aggression": "float",
    "max_order_qty": "int",
    "price_tick": "float",
}
# 返回键 -> 代码缺省（件三）
_CLOSE_POLICY_DEFAULTS = {
    "has_limit_band": True,
    "opponent_depth": 1,
    "aggression": 0.10,
    "max_order_qty": 100000,
    "price_tick": 0.001,   # 最小价位；round_close_price 缺省保留 3 位
}
# 值合法性（越界/非法值 → 回落缺省并留痕）
_CLOSE_POLICY_VALID = {
    "opponent_depth": lambda v: v >= 1,
    "aggression": lambda v: 0 <= v <= 1,
    "max_order_qty": lambda v: v >= 1,
    "price_tick": lambda v: v > 0,
}

# 返回键 -> (system_config 键, 类型, 缺省)
_RETRY_CONFIG = {
    "retry_interval_s": ("close_all_retry_interval_s", "int", 5),
    "retry_max": ("close_all_retry_max", "int", 10),
    "deadline_s": ("close_all_deadline_s", "int", 120),
    "no_quote_empty_max": ("no_quote_empty_max", "int", 6),
    "drain_timeout_s": ("drain_timeout_s", "int", 5),
}


def _coerce(raw, typ: str):
    """system_config.value(str) → 强类型；空 / 无法解析 / 非有限 → None（调用方回落缺省）。"""
    if raw is None:
        return None
    s = str(raw).strip()
    try:
        if typ == "bool":
            low = s.lower()
            if low in ("true", "1", "yes", "on"):
                return True
            if low in ("false", "0", "no", "off"):
                return False
            return None
        if typ == "int":
            return int(s)
        if typ == "float":
            v = float(s)
            return v if math.isfinite(v) else None
    except (TypeError, ValueError):
        return None
    return None


def get_close_policy(provider, category=None) -> dict:
    """本 provider 的平仓品种规则（件三配置属性）。

    读键 `close_policy.{provider}[.{category}].{attr}`：带 category 时优先 category 级键、
    缺失回落 provider 级键，再回落代码缺省。缺行 / DB 失败 / 值非法 → 回落缺省 + 聚合一条
    `logger.warning`（fail-open 留痕、非静默、不刷屏）。
    """
    provider = str(provider or "").strip()
    base = f"close_policy.{provider}"
    seg = f"{base}.{category}" if category else base
    out = dict(_CLOSE_POLICY_DEFAULTS)
    cands: dict[str, list[str]] = {}
    all_keys: list[str] = []
    for attr in _CLOSE_POLICY_DEFAULTS:
        ks = [f"{seg}.{attr}"]
        if category:
            ks.append(f"{base}.{attr}")
        cands[attr] = ks
        all_keys.extend(ks)
    try:
        from src.data_platform.db import get_conn
        with get_conn() as conn:
            cur = conn.execute(
                "SELECT key, value FROM system_config WHERE key = ANY(%s)", (all_keys,))
            rows = dict(cur.fetchall())
    except Exception as e:
        logger.warning("get_close_policy 读 system_config 失败（provider=%s category=%s，"
                       "回落代码缺省）: %s", provider, category, e)
        return out
    missing: list[str] = []
    invalid: list[str] = []
    for attr, default in _CLOSE_POLICY_DEFAULTS.items():
        typ = _CLOSE_POLICY_TYPES[attr]
        hit = False
        for k in cands[attr]:
            if k not in rows:
                continue
            parsed = _coerce(rows[k], typ)
            valid = _CLOSE_POLICY_VALID.get(attr)
            if parsed is None or (valid is not None and not valid(parsed)):
                invalid.append(k)   # 命中但非法 → 回落缺省，不再尝试更低优先级
                hit = True
                break
            out[attr] = parsed
            hit = True
            break
        if not hit:
            missing.append(attr)
    if missing or invalid:
        logger.warning("get_close_policy 回落代码缺省 provider=%s category=%s 缺行=%s 非法=%s",
                       provider, category, missing, invalid)
    return out


def get_retry_config() -> dict:
    """急停重试编排参数（件五）。返回键：
    retry_interval_s / retry_max / deadline_s / no_quote_empty_max / drain_timeout_s。

    读 system_config 键 `close_all_retry_interval_s / close_all_retry_max /
    close_all_deadline_s / no_quote_empty_max / drain_timeout_s`；缺行 / DB 失败 / 值非法
    （含 <1）→ 回落代码缺省 + `logger.warning`（fail-open 留痕、非静默）。
    """
    out = {k: spec[2] for k, spec in _RETRY_CONFIG.items()}
    keys = [spec[0] for spec in _RETRY_CONFIG.values()]
    try:
        from src.data_platform.db import get_conn
        with get_conn() as conn:
            cur = conn.execute(
                "SELECT key, value FROM system_config WHERE key = ANY(%s)", (keys,))
            rows = dict(cur.fetchall())
    except Exception as e:
        logger.warning("get_retry_config 读 system_config 失败（回落代码缺省）: %s", e)
        return out
    missing: list[str] = []
    invalid: list[str] = []
    for k, (cfg_key, typ, default) in _RETRY_CONFIG.items():
        if cfg_key not in rows:
            missing.append(cfg_key)
            continue
        v = _coerce(rows[cfg_key], typ)
        if v is None or v < 1:
            invalid.append(cfg_key)
            continue
        out[k] = v
    if missing or invalid:
        logger.warning("get_retry_config 回落代码缺省 缺行=%s 非法=%s", missing, invalid)
    return out
