"""批36a-1：风控规则参数 schema 单源（写侧校验/消费侧钳位/前端元数据下发 三用）。

执法断层背景（方案盲审 A-P1-2）：registry 三类（max_position 等）load_rules_from_db
全仓零调用=从未执行；执法三类（global/etf_conv/crypto 参数组）此前无编辑 UI。用户裁定
（2026-09-17）=活参数入 UI：六类统一入下拉（registry 三类标注未生效）+本 schema 驱动
动态表单（前端纯渲染，新类型零前端改动——schema 单源铁律）。

条目形状（批36b-α/33b 注册表协同约定参照）：typed dict——
  key/dtype(float|int|bool|enum)/lo/hi/lo_open（下界开区间）/
  default（与 DEFAULT_RULES 对齐）/step/precision/enum/values/optional/
  percent（批45：True=0~1 比例键——前端显示层 ×100 百分比化，存储仍 0~1）
命名冲突钉：daily_loss_limit 在 global/crypto=比例(0,1]，registry 侧绝对额键名是
max_loss——查表恒 (type,key) 复合键，禁平铺。
"""
from __future__ import annotations

# 消费面标注：active=执法三类（RiskControl 载入消费）；registry=存储未生效（标注展示）
RISK_PARAM_SCHEMA: dict[str, dict] = {
    "global": {
        "active": True,
        "params": [
            {"key": "max_drawdown", "dtype": "float", "lo": 0, "hi": 1, "lo_open": True,
             "default": 0.15, "step": 0.01, "precision": 4, "percent": True},
            {"key": "daily_loss_limit", "dtype": "float", "lo": 0, "hi": 1, "lo_open": True,
             "default": 0.05, "step": 0.01, "precision": 4, "percent": True},
        ],
    },
    "etf_conv": {
        "active": True,
        "params": [
            {"key": "single_position_pct", "dtype": "float", "lo": 0, "hi": 1, "lo_open": True,
             "default": 0.15, "step": 0.01, "precision": 4, "percent": True},
            {"key": "max_trades_per_day", "dtype": "int", "lo": 0, "hi": 10000,
             "default": 20, "step": 1, "precision": 0, "zero_note": True},   # 0=不限制（:316 falsy 跳过）
            {"key": "max_single_amount", "dtype": "float", "lo": 0, "hi": 1e12, "lo_open": True,
             "default": 100000, "step": 10000, "precision": 0},
            {"key": "strict_stop_loss", "dtype": "bool", "default": True, "optional": True},
        ],
    },
    "crypto": {
        "active": True,
        "params": [
            {"key": "leverage_max", "dtype": "float", "lo": 1, "hi": 100,
             "default": 5, "step": 1, "precision": 0},
            {"key": "margin_mode", "dtype": "enum", "values": ["crossed", "isolated"],
             "default": "isolated", "optional": True},
            {"key": "pin_protection", "dtype": "bool", "default": True, "optional": True},
            {"key": "daily_loss_limit", "dtype": "float", "lo": 0, "hi": 1, "lo_open": True,
             "default": 0.05, "step": 0.01, "precision": 4, "percent": True},
            {"key": "max_single_amount", "dtype": "float", "lo": 0, "hi": 1e12, "lo_open": True,
             "default": 500000, "step": 10000, "precision": 0, "optional": True},   # DEFAULT 无键、:373 .get 消费
        ],
    },
    # —— registry 三类：存储未生效（load_rules_from_db 零调用——激活属另批裁定）——
    "max_position": {
        "active": False,
        "params": [
            {"key": "max_pct", "dtype": "float", "lo": 0, "hi": 1, "lo_open": True,
             "default": 0.1, "step": 0.01, "precision": 4, "percent": True},
        ],
    },
    "max_single_order": {
        "active": False,
        "params": [
            {"key": "max_amount", "dtype": "float", "lo": 0, "hi": 1e12, "lo_open": True,
             "default": 100000, "step": 10000, "precision": 0},
        ],
    },
    "daily_loss_limit": {
        "active": False,
        "params": [
            {"key": "max_loss", "dtype": "float", "lo": 0, "hi": 1e12, "lo_open": True,
             "default": 50000, "step": 10000, "precision": 0},   # 绝对额（与 global 同名键比例语义不同——复合键钉）
        ],
    },
}


def schema_for_type(rtype: str) -> dict | None:
    return RISK_PARAM_SCHEMA.get(rtype)


def validate_params(rtype: str, params_raw) -> tuple[dict, str]:
    """写侧校验（批28-7/mgmt.py 三层模板）。返回 (干净 params dict, 错误消息)；
    错误消息非空=拒绝（400 RISK_PARAM_INVALID），detail 由调用方透传。"""
    import json
    from math import isfinite as math_isfinite
    spec = schema_for_type(rtype)
    if spec is None:
        return {}, f"未知规则类型: {rtype}"
    try:
        params = json.loads(params_raw) if isinstance(params_raw, str) else params_raw
    except (json.JSONDecodeError, TypeError):
        return {}, "params 不是合法 JSON"
    if not isinstance(params, dict):
        return {}, "params 须为 JSON 对象（键值对）"
    known = {p["key"]: p for p in spec["params"]}
    out: dict = {}
    for k, v in params.items():
        p = known.get(k)
        if p is None:
            return {}, f"未知参数: {k}"
        dtype = p["dtype"]
        if dtype == "bool":
            if not isinstance(v, bool):
                return {}, f"{k} 须为布尔值（true/false）"
            out[k] = v
        elif dtype == "enum":
            if v not in p["values"]:
                return {}, f"{k} 须为 {'/'.join(p['values'])}"
            out[k] = v
        else:
            if v is None and p.get("optional"):
                continue   # B-P2-3：可选键显式清空=不配置（el-input-number 清空得 null）
            if isinstance(v, bool) or not isinstance(v, (int, float)):
                return {}, f"{k} 须为数字"
            if not math_isfinite(v):
                return {}, f"{k} 须为有限数字（NaN/无穷不可用）"   # A-P1-3：NaN 穿透=护栏静默失效
            if "lo" in p:
                if v < p["lo"] or (v == p["lo"] and p.get("lo_open")):
                    return {}, (f"{k} 须大于 {p['lo']}（不含）" if p.get("lo_open") else f"{k} 不得小于 {p['lo']}")
            if "hi" in p and v > p["hi"]:
                return {}, f"{k} 不得大于 {p['hi']}"
            if dtype == "int" and not float(v).is_integer():
                return {}, f"{k} 须为整数"
            out[k] = int(v) if dtype == "int" else float(v)
    return out, ""


class RuleSanitizer:
    """消费侧钳位（方案盲审 A-P0-1 三层语义）——RiskControl 载入路径专用。
    按值指纹去抖告警（每 type+key 一次，仅日志不入 notify——B-P1-2 防多进程×60s 刷屏）：
      ①非 dict/坏 JSON → 该 type 整体回落 DEFAULT（逐 type，非整表）
      ②键值类型非法（数字键收 str/bool 等）→ 该键回落缺省
      ③数值超界 → 钳边界"""

    def __init__(self, logger):
        self._logger = logger
        self._warned: set[tuple] = set()

    def _warn_once(self, fingerprint: tuple, msg: str) -> None:
        if fingerprint not in self._warned:
            self._warned.add(fingerprint)
            self._logger.warning("风控参数钳位: %s（同因仅告警一次）", msg)

    def sanitize_type(self, rtype: str, raw_params) -> dict:
        """单 type 载入清洗：返回可安全 {**base, **out} 的 dict。任何失败=空 dict（回落 DEFAULT）。"""
        spec = schema_for_type(rtype)
        if spec is None:
            return {}
        if not isinstance(raw_params, dict):   # ①坏 JSON 已在上游 json.loads 挡（None）；非 dict（如 [1]/"5"）在此挡
            self._warn_once((rtype, "__shape__"), f"type={rtype} params 非对象，整组回落默认")
            return {}
        known = {p["key"]: p for p in spec["params"]}
        out: dict = {}
        for k, v in raw_params.items():
            p = known.get(k)
            if p is None:
                self._warn_once((rtype, k), f"type={rtype} 未知参数 {k}，忽略")
                continue
            dtype = p["dtype"]
            if dtype == "bool":
                if isinstance(v, bool):
                    out[k] = v
                else:
                    self._warn_once((rtype, k, type(v).__name__), f"type={rtype}.{k} 非布尔，回落默认 {p['default']}")
                continue
            if dtype == "enum":
                if v in p["values"]:
                    out[k] = v
                else:
                    self._warn_once((rtype, k, str(v)[:20]), f"type={rtype}.{k} 非法枚举值，回落默认 {p['default']}")
                continue
            if isinstance(v, bool) or not isinstance(v, (int, float)) or v != v:   # ②字符串数字/NaN——check_order 崩溃源与静默失效源（A-P1-3）
                self._warn_once((rtype, k, type(v).__name__), f"type={rtype}.{k} 非有限数值，回落默认 {p['default']}")
                continue
            val = float(v)
            if dtype == "int" and not val.is_integer():
                self._warn_once((rtype, k, val), f"type={rtype}.{k} 非整数，回落默认 {p['default']}")
                continue
            val = int(val) if dtype == "int" else val
            lo, hi = p.get("lo"), p.get("hi")
            clamped = val
            if lo is not None and (val < lo or (val == lo and p.get("lo_open"))):
                clamped = None   # 开区间下界无法钳（钳 lo 仍违反开区间）→ 回落缺省
            elif hi is not None and val > hi:
                clamped = hi
            if clamped is None or clamped != val:
                action = "回落默认" if clamped is None else f"钳至 {clamped}"
                self._warn_once((rtype, k, val), f"type={rtype}.{k}={val} 超界，{action}")
                if clamped is None:
                    continue   # 不入 out=合并时用 base 缺省
            out[k] = clamped
        return out
