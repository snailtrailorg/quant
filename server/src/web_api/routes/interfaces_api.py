"""批189 三实体接口路由（md/td/data-source）。鉴权在此，SQL 在 iface_store。"""
from __future__ import annotations

import logging
import subprocess

from fastapi import APIRouter, Body, Depends

from src.data_platform import iface_store as S

from ..auth import require_perm
from ..errors import ApiError

logger = logging.getLogger(__name__)
router = APIRouter()


def _probe(role: str, provider: str) -> bool:
    """启用前载体探针：该 provider 在对应域是否**有实际端口**（不是仅有实体）。

    仅在 enabled 时经 validate_payload 调用：无端口的实体（如 EMT 交易 P4c 前）是可保存
    的「待接入」草稿，但不可启用——否则 enabled=true 却没有进程消费命令 = 静默丢单。
    """
    try:
        import src.providers as _p
        _p.ensure_registered()
        from src.registry import DOMAIN_MARKET, DOMAIN_TRADING, pick
        domain, need = ((DOMAIN_MARKET, {"market_data"}) if role == "market"
                        else (DOMAIN_TRADING, {"execution"}))
        pick(domain, provider, need, "plugin")
        return True
    except Exception as e:  # noqa: BLE001 —— 未登记/无端口=无载体（留痕：接线错误不得静默成 400）
        logger.warning("载体探针失败 provider=%s role=%s: %s", provider, role, e)
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


def _providers(role: str) -> list[dict]:
    """该域已登记的 provider 及端口就绪态（registry 派生；目录不写死字面量）。

    ready=False = 有实体但无端口（EMT 交易 P4c 前）：UI 标「待接入」，可存草稿不可启用。
    """
    import src.providers as p
    p.ensure_registered()
    from src.registry import DOMAIN_MARKET, DOMAIN_TRADING, pick, registered
    domain, need = ((DOMAIN_MARKET, {"market_data"}) if role == "market"
                    else (DOMAIN_TRADING, {"execution"}))
    out = []
    for prov in sorted({name for (dom, name) in registered() if dom == domain}):
        try:
            pick(domain, prov, need, "plugin")
            ready = True
        except Exception:  # noqa: BLE001 —— 实体在、端口缺 = 待接入
            ready = False
        out.append({"provider": prov, "ready": ready})
    return out


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


@router.get("/api/interfaces/status")
def iface_status(kind: str, _u=Depends(require_perm("read"))):
    """接口运行态只读（M6a 状态卡）：running/working/connected，不判活不做阈值。"""
    from src.data_platform.iface_status import status_of
    if kind not in ("md", "td"):
        raise ApiError(400, "KIND_UNKNOWN", kind)
    codes = [r["code"] for r in _wrap(S.list_interfaces, kind)]
    return {"items": status_of(kind, codes)}


@router.get("/api/md-interfaces/providers")
def md_providers(_u=Depends(require_perm("read"))):
    return {"items": _providers("market")}


@router.get("/api/td-interfaces/providers")
def td_providers(_u=Depends(require_perm("read"))):
    return {"items": _providers("trade")}


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
