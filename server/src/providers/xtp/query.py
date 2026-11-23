"""XTP 在途委托查询（薄适配，不进 vnpy 源码）。

TdApi 绑定已暴露 queryUnfinishedOrdersEx/onQueryOrderEx；vnpy gateway 未包装。
本模块按次临时挂 onQueryOrderEx，收集 last=True 前的分页结果。

⚠️ 依赖 pybind 回调按实例属性动态查找（vnpy 的 XtpTdApi 本身是子类）；
上产前须在真实 TD 会话实测一次，失败则改为在插件内继承 XtpTdApi 固定方法。
"""
from __future__ import annotations

import threading
import time

_LOCK = threading.Lock()


def query_unfinished_orders(td_api, timeout_s: float = 3.0) -> dict[str, dict]:
    """返回 {vt_orderid/order_xtp_id: raw dict}；失败/超时返回已收部分。"""
    if td_api is None or not hasattr(td_api, "queryUnfinishedOrdersEx"):
        return {}
    with _LOCK:
        buf: dict[str, dict] = {}
        done = threading.Event()
        prev = getattr(td_api, "onQueryOrderEx", None)

        def _cb(data, error, reqid, last, session):
            if data:
                oid = str(data.get("order_xtp_id") or data.get("orderid") or "")
                if oid:
                    buf[oid] = data
            if last:
                done.set()

        try:
            td_api.onQueryOrderEx = _cb
        except Exception:
            return {}
        try:
            rid = int(time.time() * 1000) % 2_000_000_000
            td_api.queryUnfinishedOrdersEx(rid, int(getattr(td_api, "session_id", 0) or 0))
            done.wait(max(0.1, float(timeout_s)))
            return dict(buf)
        except Exception:
            return dict(buf)
        finally:
            try:
                if prev is None:
                    del td_api.onQueryOrderEx
                else:
                    td_api.onQueryOrderEx = prev
            except Exception:
                pass
