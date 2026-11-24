"""XTP 在途委托查询（薄适配，不进 vnpy 源码）。

TdApi 绑定已暴露 queryUnfinishedOrdersEx/onQueryOrderEx；vnpy gateway 未包装。

⚠️ 参数顺序（反汇编 libxtptraderapi.so 实证）：
    QueryUnfinishedOrdersEx(uint64 session_id, int request_id)
  —— session 在前、request_id 在后。传反会把时间戳当 session 去查槽位，
  XTP SessionMgr::GetSession 无边界检查 → 数组越界 SIGSEGV（2026-11-24 生产事故）。
  且登录完成前 session_id=0，禁止查询。

回调采用**一次性永久安装**的 dispatcher（按 reqid 分发），不再动态挂/删实例属性，
避免迟到回调打到已删除方法。
"""
from __future__ import annotations

import threading
import time

_LOCK = threading.Lock()


def _ensure_hook(td_api) -> dict:
    st = getattr(td_api, "_qx_state", None)
    if isinstance(st, dict) and "pending" in st:
        return st
    st = {"pending": {}, "lock": threading.Lock()}

    def _cb(data, error, reqid, last, session):
        try:
            with st["lock"]:
                ent = st["pending"].get(int(reqid))
                if ent is None:
                    return
                if data:
                    oid = str(data.get("order_xtp_id") or data.get("orderid") or "")
                    if oid:
                        ent["buf"][oid] = data
                if last:
                    ent["event"].set()
        except Exception:   # noqa: BLE001 —— C++ 回调线程禁抛
            pass

    try:
        td_api.onQueryOrderEx = _cb
        td_api._qx_state = st
    except Exception:   # noqa: BLE001
        return {"pending": {}, "lock": threading.Lock()}
    return st


def query_unfinished_orders(td_api, timeout_s: float = 3.0) -> dict[str, dict]:
    """返回 {order_xtp_id: raw dict}；未登录/失败/超时返回已收部分。"""
    if td_api is None or not hasattr(td_api, "queryUnfinishedOrdersEx"):
        return {}
    sid = int(getattr(td_api, "session_id", 0) or 0)
    if sid <= 0:
        return {}   # 未登录：XTP 会话槽不存在，调用会越界崩溃
    with _LOCK:
        st = _ensure_hook(td_api)
        rid = int(time.time() * 1000) % 2_000_000_000
        ent = {"buf": {}, "event": threading.Event()}
        with st["lock"]:
            st["pending"][rid] = ent
        try:
            # session 在前、request_id 在后（见模块 docstring 反汇编证据）
            td_api.queryUnfinishedOrdersEx(sid, rid)
            ent["event"].wait(max(0.1, float(timeout_s)))
            return dict(ent["buf"])
        except Exception:   # noqa: BLE001
            return dict(ent["buf"])
        finally:
            with st["lock"]:
                st["pending"].pop(rid, None)   # 迟到回调按 reqid 丢弃，不碰方法
