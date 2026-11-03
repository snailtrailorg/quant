"""标的筛选 v2 路由（批 164b 件5——分层重构版：SQL 全沉 data_platform/screen.py）。"""
from fastapi import APIRouter, Depends
from pydantic import BaseModel

from ..auth import audit_log, require_perm
from ..errors import ApiError
from src.data_platform.screen import (
    FIELDS, compile_conditions, preset_list, preset_create,
    preset_get, preset_update, preset_delete, run_query)

router = APIRouter()


class PresetBody(BaseModel):
    name: str
    category: str
    definition: dict


def _shape_source(definition: dict) -> str:
    if "filter" in definition:
        return "dsl"
    return "builder"


def _validate_dry_run(category: str, definition: dict) -> None:
    src = _shape_source(definition)
    if src == "builder":
        try:
            compile_conditions(category, definition)
        except ValueError as e:
            raise ApiError(400, "SCREEN_PRESET_INVALID", str(e))
    else:
        from ...strategy_framework.cross_section import validate_cross_section_expr
        for expr in (definition.get("filter") or "", definition.get("rank") or ""):
            if not expr:
                continue
            try:
                validate_cross_section_expr(expr, category)
            except ValueError as e:
                code = str(e).split(":", 1)[0]
                raise ApiError(400, code if code.startswith("XSEC_") else "SCREEN_PRESET_INVALID", str(e))


@router.get("/api/screen/presets")
def list_presets(category: str, payload: dict = Depends(require_perm("read"))):
    return preset_list(category, payload["uid"])


@router.post("/api/screen/presets")
def create_preset(body: PresetBody, payload: dict = Depends(require_perm("screen_preset"))):
    if body.category not in FIELDS:
        raise ApiError(400, "SCREEN_PRESET_CATEGORY", "unknown category")
    _validate_dry_run(body.category, body.definition)
    src = _shape_source(body.definition)
    try:
        pid = preset_create(body.name, body.category, src, body.definition, payload["uid"])
    except Exception as e:
        # 只把唯一冲突转 409；其余（连接/权限/约束）如实抛出（评审 P1：勿把 DB 故障伪装成重名）
        if "UniqueViolation" in type(e).__name__ or "duplicate key" in str(e):
            raise ApiError(409, "SCREEN_PRESET_DUP", "同名方案已存在")
        raise
    audit_log(payload["username"], "screen_preset_create", body.category + "/" + body.name)
    return {"id": pid, "source": src}


@router.patch("/api/screen/presets/{pid}")
def update_preset(pid: int, body: PresetBody,
                  payload: dict = Depends(require_perm("screen_preset"))):
    row = preset_get(pid)
    if row is None:
        raise ApiError(404, "SCREEN_PRESET_NOT_FOUND", "方案不存在")
    if row[3] == "builtin":
        raise ApiError(403, "SCREEN_PRESET_BUILTIN", "出厂预置不可修改——另存为新方案")
    if row[4] != payload["uid"] and payload.get("role") != "admin":
        raise ApiError(403, "SCREEN_PRESET_NOT_YOURS", "仅本人方案可修改")
    _validate_dry_run(body.category, body.definition)
    src = _shape_source(body.definition)
    preset_update(pid, body.name, body.category, src, body.definition)
    audit_log(payload["username"], "screen_preset_update", "#" + str(pid))
    return {"id": pid, "source": src}


@router.delete("/api/screen/presets/{pid}")
def delete_preset(pid: int, payload: dict = Depends(require_perm("screen_preset"))):
    row = preset_get(pid)
    if row is None:
        raise ApiError(404, "SCREEN_PRESET_NOT_FOUND", "方案不存在")
    if row[3] == "builtin":
        raise ApiError(403, "SCREEN_PRESET_BUILTIN", "出厂预置不可删除")
    if row[4] != payload["uid"] and payload.get("role") != "admin":
        raise ApiError(403, "SCREEN_PRESET_NOT_YOURS", "仅本人方案可删除")
    preset_delete(pid)
    audit_log(payload["username"], "screen_preset_delete", "#" + str(pid) + " " + str(row[1]))
    return {"deleted": pid}


@router.get("/api/screen/fields")
def list_fields(category: str, payload: dict = Depends(require_perm("read"))):
    flds = FIELDS.get(category)
    if flds is None:
        raise ApiError(400, "SCREEN_PRESET_CATEGORY", "unknown category")
    return {"fields": [
        {"name": k, "label": v["label"], "type": v["type"], "group": v["group"],
         "nullable": v["nullable"], "dead": category == "listed_fund"}
        for k, v in flds.items()]}


class RunBody(BaseModel):
    category: str
    definition: dict
    limit: int = 100


class RunDslBody(BaseModel):
    category: str
    filter: str
    rank: str | None = None
    limit: int = 100


@router.post("/api/screen/run-dsl")
def run_dsl(body: RunDslBody, payload: dict = Depends(require_perm("read"))):
    """横截面 DSL 筛选（164c）——与 /run 同族规（limit 帽/权限键/错误码形态）。"""
    if body.limit > 500:
        raise ApiError(400, "SCREEN_RUN_LIMIT", "limit 上限 500")
    if body.category not in FIELDS:
        raise ApiError(400, "SCREEN_PRESET_CATEGORY", "unknown category")
    if not (body.filter or "").strip():
        raise ApiError(400, "SCREEN_RUN_EMPTY", "过滤表达式不能为空")
    from src.strategy_framework.cross_section import run_cross_section
    try:
        rows = run_cross_section(body.category, body.filter, body.rank, body.limit)
    except (ValueError, IndexError) as e:
        code = str(e).split(":", 1)[0]
        if not code.startswith("XSEC_"):
            code = "XSEC_SYNTAX"
        raise ApiError(400, code, str(e))
    return {"rows": rows, "count": len(rows)}


@router.post("/api/screen/run")
def run_screen(body: RunBody, payload: dict = Depends(require_perm("read"))):
    if body.limit > 500:
        raise ApiError(400, "SCREEN_RUN_LIMIT", "limit 上限 500")
    if _shape_source(body.definition) != "builder":
        raise ApiError(400, "SCREEN_RUN_DSL", "DSL 式请走 run-dsl（164c）")
    try:
        where, params, null_fields = compile_conditions(body.category, body.definition)
    except ValueError as e:
        msg = str(e)
        if "unknown field" in msg:
            raise ApiError(400, "SCREEN_FIELD_UNKNOWN", msg)
        if "仅数值" in msg or "percentile 仅" in msg:
            raise ApiError(400, "SCREEN_FIELD_TYPE_MISMATCH", msg)
        raise ApiError(400, "SCREEN_PRESET_INVALID", msg)
    rows_spec = body.definition.get("rows", [])
    fields = [r["field"] for r in rows_spec]
    # 排序：首个数值条件的字段+方向（双低前百需按双低升序——评审 P1）
    order_by = None
    _DIR = {"<": "ASC", "<=": "ASC", "percentile_bottom": "ASC",
            ">": "DESC", ">=": "DESC", "percentile_top": "DESC"}
    for r in rows_spec:
        spec = FIELDS.get(body.category, {}).get(r.get("field"))
        if spec and spec.get("type") == "num" and r.get("op") in _DIR:
            order_by = f"q.{r['field']} {_DIR[r['op']]} NULLS LAST"
            break
    return run_query(body.category, fields, where, params, body.limit, null_fields, order_by)
