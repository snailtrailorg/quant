"""标的筛选 v2 路由（批 164b 件5）：presets CRUD + /run + fields。

设计真源：flow/任务/批164b-条件构建器与筛选方案.md v3（步骤 5.5/6）。
权限：读面=read 既有键；写面=screen_preset 专键；source 判别=形状单源。
"""
from fastapi import APIRouter, Depends
from pydantic import BaseModel

from src.data_platform.db import get_conn
from ..auth import audit_log, require_perm
from ..errors import ApiError
from src.data_platform.screen import FIELDS, compile_conditions, build_base_from
import json

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
    with get_conn() as conn:
        cur = conn.execute(
            "SELECT id, name, category, source, definition, created_by, updated_at "
            "FROM screen_preset WHERE category = %s "
            "AND (source = 'builtin' OR created_by = %s) "
            "ORDER BY source DESC, updated_at DESC", (category, payload["uid"]))
        rows = cur.fetchall()
    out = []
    for r in rows:
        d = r[4] if isinstance(r[4], dict) else json.loads(r[4])
        skipped = []
        flds = FIELDS.get(r[2], {})
        if "rows" in d:
            skipped = [row.get("field") for row in d["rows"] if row.get("field") not in flds]
            d["rows"] = [row for row in d["rows"] if row.get("field") in flds]
        out.append({"id": r[0], "name": r[1], "category": r[2], "source": r[3],
                    "definition": d, "created_by": r[5], "updated_at": str(r[6]),
                    "skipped_fields": skipped})
    return out


@router.post("/api/screen/presets")
def create_preset(body: PresetBody, payload: dict = Depends(require_perm("screen_preset"))):
    if body.category not in FIELDS:
        raise ApiError(400, "SCREEN_PRESET_CATEGORY", "unknown category")
    _validate_dry_run(body.category, body.definition)
    src = _shape_source(body.definition)
    with get_conn() as conn:
        try:
            cur = conn.execute(
                "INSERT INTO screen_preset (name, category, source, definition, created_by) "
                "VALUES (%s,%s,%s,%s,%s) RETURNING id",
                (body.name, body.category, src, json.dumps(body.definition), payload["uid"]))
            conn.commit()
        except Exception:
            raise ApiError(409, "SCREEN_PRESET_DUP", "同名方案已存在")
    audit_log(payload["username"], "screen_preset_create", body.category + "/" + body.name)
    return {"id": cur.fetchone()[0], "source": src}


@router.patch("/api/screen/presets/{pid}")
def update_preset(pid: int, body: PresetBody,
                  payload: dict = Depends(require_perm("screen_preset"))):
    with get_conn() as conn:
        cur = conn.execute("SELECT source, created_by FROM screen_preset WHERE id = %s", (pid,))
        row = cur.fetchone()
        if row is None:
            raise ApiError(404, "SCREEN_PRESET_NOT_FOUND", "方案不存在")
        if row[0] == "builtin":
            raise ApiError(403, "SCREEN_PRESET_BUILTIN", "出厂预置不可修改——另存为新方案")
        if row[1] != payload["uid"] and payload.get("role") != "admin":
            raise ApiError(403, "SCREEN_PRESET_NOT_YOURS", "仅本人方案可修改")
        _validate_dry_run(body.category, body.definition)
        src = _shape_source(body.definition)
        conn.execute(
            "UPDATE screen_preset SET name=%s, category=%s, source=%s, definition=%s, updated_at=now() "
            "WHERE id = %s", (body.name, body.category, src, json.dumps(body.definition), pid))
        conn.commit()
    audit_log(payload["username"], "screen_preset_update", "#" + str(pid))
    return {"id": pid, "source": src}


@router.delete("/api/screen/presets/{pid}")
def delete_preset(pid: int, payload: dict = Depends(require_perm("screen_preset"))):
    with get_conn() as conn:
        cur = conn.execute("SELECT source, created_by, name FROM screen_preset WHERE id = %s", (pid,))
        row = cur.fetchone()
        if row is None:
            raise ApiError(404, "SCREEN_PRESET_NOT_FOUND", "方案不存在")
        if row[0] == "builtin":
            raise ApiError(403, "SCREEN_PRESET_BUILTIN", "出厂预置不可删除")
        if row[1] != payload["uid"] and payload.get("role") != "admin":
            raise ApiError(403, "SCREEN_PRESET_NOT_YOURS", "仅本人方案可删除")
        conn.execute("DELETE FROM screen_preset WHERE id = %s", (pid,))
        conn.commit()
    audit_log(payload["username"], "screen_preset_delete", "#" + str(pid) + " " + str(row[2]))
    return {"deleted": pid}


@router.get("/api/screen/fields")
def list_fields(category: str, payload: dict = Depends(require_perm("read"))):
    flds = FIELDS.get(category)
    if flds is None:
        raise ApiError(400, "SCREEN_PRESET_CATEGORY", "unknown category")
    return {"fields": [
        {"name": k, "label": v["label"], "type": v["type"], "group": v["group"],
         "nullable": v["nullable"],
         "dead": category == "listed_fund"}
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
    base = build_base_from(body.category, fields or None)
    q = "SELECT * FROM (" + base + ") q" + where + " ORDER BY ts_code LIMIT " + str(int(body.limit))
    with get_conn() as conn:
        cur = conn.execute(q, params)
        cols = [d[0] for d in cur.description]
        rows = [dict(zip(cols, r)) for r in cur.fetchall()]
        if null_fields:
            null_checks = " OR ".join(f + " IS NULL" for f in null_fields)
            cur = conn.execute("SELECT count(*) FROM (" + base + ") q WHERE " + null_checks)
            nulls = cur.fetchone()[0]
        else:
            nulls = 0
    return {"rows": rows, "nulls_excluded": nulls, "count": len(rows)}
