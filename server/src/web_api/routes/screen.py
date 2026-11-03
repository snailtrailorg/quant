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
        try:
            from ...strategy_framework.cross_section import validate_cross_section_expr
            validate_cross_section_expr(definition.get("filter", ""), category)
        except ImportError:
            raise ApiError(400, "SCREEN_PRESET_DSL_PENDING", "DSL 校验器随 164c 交付")
        except ValueError as e:
            raise ApiError(400, "SCREEN_PRESET_INVALID", str(e))


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
    except Exception:
        raise ApiError(409, "SCREEN_PRESET_DUP", "同名方案已存在")
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
    fields = [r["field"] for r in body.definition.get("rows", [])]
    return run_query(body.category, fields, where, params, body.limit, null_fields)
