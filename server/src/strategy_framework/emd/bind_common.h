// 批 63 P4：EMQ/EMT pybind11 绑定公共头——回调覆写守卫宏（两 cpp 共用防漂移，快审 P2-3）。
// 对标 XTP GuardedXtpMdApi「回调线程永不抛」：Python 覆写任何异常都不得穿透 SDK 回调帧
// （裸抛 = error_already_set 逃逸 → std::terminate / SIGABRT 整进程崩）。
// get_override 拿到覆写后 try/catch 兜底，discard_as_unraisable 打印并吞掉。
#pragma once
#include <pybind11/pybind11.h>

#define EMT_OVERRIDE_GUARDED(cname, fn, ...)                                      \
    do {                                                                         \
        py::gil_scoped_acquire gil;                                              \
        py::function _f = py::get_override(static_cast<const cname*>(this), fn); \
        if (!_f) return;                                                         \
        try {                                                                    \
            _f(__VA_ARGS__);                                                     \
        } catch (py::error_already_set& _e) {                                    \
            _e.discard_as_unraisable(fn);                                        \
        }                                                                        \
    } while (0)

// 兼容别名（bind_quote.cpp 历史用 EMQ_ 前缀——P1 产物零改动）
#define EMQ_OVERRIDE_GUARDED EMT_OVERRIDE_GUARDED
