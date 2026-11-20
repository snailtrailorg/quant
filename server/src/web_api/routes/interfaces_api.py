"""批189 三实体接口路由（md/td/data-source）。鉴权在此，SQL 在 iface_store。"""
from __future__ import annotations

import subprocess

from fastapi import APIRouter, Body, Depends

from src.data_platform import iface_store as S

from ..auth import require_perm
from ..errors import ApiError

router = APIRouter()


def _probe(role: str, provider: str) -> bool:
    """载体探针（层 4 组装）：registry 插件 ∪ legacy md_gateway 注册。"""
    try:
        import src.providers as _p
        _p.ensure_registered()
        from src.registry import pick
        pick(domain=role, caps={"market_data" if role == "market" else "execution"},
             provider=provider)
        return True
    except Exception:  # noqa: BLE001
        pass
    if role == "market":
        try:
            from src.strategy_framework.md_gateway import list_md_gateway_providers
            return provider in set(list_md_gateway_providers())
        except Exception:  # noqa: BLE001
            return False
    return False


def _stop(unit: str) -> None:
    subprocess.run(["systemctl", "stop", unit], check=True, timeout=30)


def _active(unit: str) -> bool:
    r = subprocess.run(["systemctl", "is-active", unit], capture_output=True, text=True, timeout=10)
    return r.stdout.strip() == "active"


def _wrap(fn, *a, **kw):
    try:
        return fn(*a, **kw)
    except S.IfaceError as e:
        raise ApiError(e.status, e.code, e.detail or e.code) from e


KIND_PATHS = {"md": "/api/md-interfaces", "td": "/api/td-interfaces", "ds": "/api/data-sources"}


@router.get("/api/md-interfaces")
def list_md(enabled: bool | None = None, market: str | None = None,
            _u=Depends(require_perm("read"))):
    return {"items": _wrap(S.list_interfaces, "md", enabled=enabled, market=market)}


@router.get("/api/td-interfaces")
def list_td(enabled: bool | None = None, market: str | None = None,
            _u=Depends(require_perm("read"))):
    return {"items": _wrap(S.list_interfaces, "td", enabled=enabled, market=market)}


@router.get("/api/data-sources")
def list_ds(enabled: bool | None = None, market: str | None = None,
            _u=Depends(require_perm("read"))):
    return {"items": _wrap(S.list_interfaces, "ds", enabled=enabled, market=market)}


@router.post("/api/md-interfaces")
def create_md(payload: dict = Body(...), _u=Depends(require_perm("system_config"))):
    return _wrap(S.create_interface, "md", payload, _probe)


@router.post("/api/td-interfaces")
def create_td(payload: dict = Body(...), _u=Depends(require_perm("system_config"))):
    return _wrap(S.create_interface, "td", payload, _probe)


@router.post("/api/data-sources")
def create_ds(payload: dict = Body(...), _u=Depends(require_perm("system_config"))):
    return _wrap(S.create_interface, "ds", payload, _probe)


@router.patch("/api/md-interfaces/{code}")
def patch_md(code: str, payload: dict = Body(...), _u=Depends(require_perm("system_config"))):
    return _wrap(S.update_interface, "md", code, payload, _probe)


@router.patch("/api/td-interfaces/{code}")
def patch_td(code: str, payload: dict = Body(...), _u=Depends(require_perm("system_config"))):
    return _wrap(S.update_interface, "td", code, payload, _probe)


@router.patch("/api/data-sources/{code}")
def patch_ds(code: str, payload: dict = Body(...), _u=Depends(require_perm("system_config"))):
    return _wrap(S.update_interface, "ds", code, payload, _probe)


@router.delete("/api/md-interfaces/{code}")
def del_md(code: str, _u=Depends(require_perm("system_config"))):
    _wrap(S.delete_interface, "md", code, _stop, _active)
    return {"ok": True}


@router.delete("/api/td-interfaces/{code}")
def del_td(code: str, _u=Depends(require_perm("system_config"))):
    _wrap(S.delete_interface, "td", code, _stop, _active)
    return {"ok": True}


@router.delete("/api/data-sources/{code}")
def del_ds(code: str, _u=Depends(require_perm("system_config"))):
    _wrap(S.delete_interface, "ds", code, _stop, _active)
    return {"ok": True}


@router.post("/api/md-interfaces/{code}/enable")
def enable_md(code: str, payload: dict = Body(...), _u=Depends(require_perm("system_config"))):
    on = bool(payload.get("enabled"))
    return _wrap(S.update_interface, "md", code, {"enabled": on}, _probe)


@router.post("/api/td-interfaces/{code}/enable")
def enable_td(code: str, payload: dict = Body(...), _u=Depends(require_perm("system_config"))):
    on = bool(payload.get("enabled"))
    return _wrap(S.update_interface, "td", code, {"enabled": on}, _probe)


@router.get("/api/md-interfaces/{code}/mappings")
def md_maps(code: str, _u=Depends(require_perm("read"))):
    return {"items": _wrap(S.list_mappings, "md", code)}


@router.get("/api/td-interfaces/{code}/mappings")
def td_maps(code: str, _u=Depends(require_perm("read"))):
    return {"items": _wrap(S.list_mappings, "td", code)}


@router.post("/api/td-interfaces/{code}/mappings")
def set_td_maps(code: str, payload: dict = Body(...), _u=Depends(require_perm("system_config"))):
    _wrap(S.set_mappings, code, payload.get("items") or [])
    return {"ok": True}
