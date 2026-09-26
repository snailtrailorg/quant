// 批 63 Phase B：EMQ 极速行情 pybind11 绑定（P2 完整面——订阅/退订/快照结构/回调）。
// 命名空间 EMQ::API；QuoteApi=纯虚接口（CreateQuoteApi 工厂），QuoteSpi=回调基类。
#include <pybind11/pybind11.h>
#include <string>
#include <vector>
#include "bind_common.h"   // 批 63 P4：EMQ_OVERRIDE_GUARDED 公共头（两 cpp 共用防漂移）
#include "quote_api.h"
#include "emt_quote_struct.h"
#include "emt_quote_data_type.h"

namespace py = pybind11;
using namespace EMQ::API;

namespace {

// char** 数组构造：SDK 签名 tickers 为 char*[]（非 const，历史遗留，实为只读——demo
// 以字符串字面量直传）。由 Python list[str] 转 C 字符串数组，生命周期覆盖本次同步调用即可。
std::vector<char*> _ticker_ptrs(py::list tickers, std::vector<std::string>& keepalive) {
    keepalive.clear();
    keepalive.reserve(py::len(tickers));
    for (py::handle t : tickers) {
        keepalive.emplace_back(py::cast<std::string>(t));
    }
    std::vector<char*> ptrs;
    ptrs.reserve(keepalive.size());
    for (auto& s : keepalive) {
        ptrs.push_back(const_cast<char*>(s.c_str()));
    }
    return ptrs;
}

// QuoteSpi trampoline：让 Python 侧继承并覆写回调。
// 队列数组（bid1_qty/ask1_qty 逐笔队列）不消费——十档已在 EMTMarketDataStruct，
// OnDepthMarketData 丢弃数组只透传结构体与计数（基类 7 参 vs 覆写 3 参，无法用
// PYBIND11_OVERRIDE 改参数量，且裸 int64_t[] pybind11 无法转换）。
class PyQuoteSpi : public QuoteSpi {
public:
    using QuoteSpi::QuoteSpi;

    void OnDepthMarketData(EMTMarketDataStruct* market_data, int64_t bid1_qty[], int32_t bid1_count,
                           int32_t max_bid1_count, int64_t ask1_qty[], int32_t ask1_count,
                           int32_t max_ask1_count) override {
        EMQ_OVERRIDE_GUARDED(QuoteSpi, "OnDepthMarketData", market_data, bid1_count, ask1_count);
    }

    void OnError(const EMTRspInfoStruct* error_info) override {
        EMQ_OVERRIDE_GUARDED(QuoteSpi, "OnError", error_info);
    }

    void OnSubMarketData(EMTSpecificTickerStruct* ticker, EMTRspInfoStruct* error_info, bool is_last) override {
        EMQ_OVERRIDE_GUARDED(QuoteSpi, "OnSubMarketData", ticker, error_info, is_last);
    }

    void OnUnSubMarketData(EMTSpecificTickerStruct* ticker, EMTRspInfoStruct* error_info, bool is_last) override {
        EMQ_OVERRIDE_GUARDED(QuoteSpi, "OnUnSubMarketData", ticker, error_info, is_last);
    }
};

// 十档数组 → Python list（Python 侧 [0:5] 取五档）。
py::list _ten(const double arr[10]) {
    py::list l;
    for (int i = 0; i < 10; i++) l.append(arr[i]);
    return l;
}

py::list _ten_i(const int64_t arr[10]) {
    py::list l;
    for (int i = 0; i < 10; i++) l.append(arr[i]);
    return l;
}

}  // namespace

PYBIND11_MODULE(emd_quote_api, m) {
    m.doc() = "东方财富 EMQ 极速行情 QuoteApi 绑定（批 63 Phase B）";

    py::enum_<EMQ_LOG_LEVEL>(m, "EMQ_LOG_LEVEL")
        .value("TRACE", EMQ_LOG_LEVEL_TRACE)
        .value("DEBUG", EMQ_LOG_LEVEL_DEBUG)
        .value("INFO", EMQ_LOG_LEVEL_INFO)
        .value("WARNING", EMQ_LOG_LEVEL_WARNING)
        .value("ERROR", EMQ_LOG_LEVEL_ERROR)
        .value("FATAL", EMQ_LOG_LEVEL_FATAL)
        .export_values();

    py::enum_<EMQ_EXCHANGE_TYPE>(m, "EMQ_EXCHANGE_TYPE")
        .value("SH", EMQ_EXCHANGE_SH)
        .value("SZ", EMQ_EXCHANGE_SZ)
        .value("SHHK", EMQ_EXCHANGE_SHHK)
        .value("SZHK", EMQ_EXCHANGE_SZHK)
        .value("BJGZ", EMQ_EXCHANGE_BJGZ)
        .value("UNKNOWN", EMQ_EXCHANGE_UNKNOWN)
        .export_values();

    py::class_<EMTRspInfoStruct>(m, "EMTRspInfoStruct")
        .def_readonly("error_id", &EMTRspInfoStruct::error_id)
        .def_property_readonly("error_msg", [](const EMTRspInfoStruct& r) {
            return std::string(r.error_msg);
        });

    py::class_<EMTSpecificTickerStruct>(m, "EMTSpecificTickerStruct")
        .def_readonly("exchange_id", &EMTSpecificTickerStruct::exchange_id)
        .def_property_readonly("ticker", [](const EMTSpecificTickerStruct& t) {
            return std::string(t.ticker);
        });

    py::class_<EMTMarketDataStruct>(m, "EMTMarketDataStruct")
        .def_readonly("exchange_id", &EMTMarketDataStruct::exchange_id)
        .def_property_readonly("ticker", [](const EMTMarketDataStruct& d) {
            return std::string(d.ticker);
        })
        .def_readonly("last_price", &EMTMarketDataStruct::last_price)
        .def_readonly("pre_close_price", &EMTMarketDataStruct::pre_close_price)
        .def_readonly("open_price", &EMTMarketDataStruct::open_price)
        .def_readonly("high_price", &EMTMarketDataStruct::high_price)
        .def_readonly("low_price", &EMTMarketDataStruct::low_price)
        .def_readonly("close_price", &EMTMarketDataStruct::close_price)
        .def_readonly("upper_limit_price", &EMTMarketDataStruct::upper_limit_price)
        .def_readonly("lower_limit_price", &EMTMarketDataStruct::lower_limit_price)
        .def_readonly("data_time", &EMTMarketDataStruct::data_time)
        .def_readonly("qty", &EMTMarketDataStruct::qty)
        .def_readonly("turnover", &EMTMarketDataStruct::turnover)
        .def_property_readonly("bid", [](const EMTMarketDataStruct& d) { return _ten(d.bid); })
        .def_property_readonly("ask", [](const EMTMarketDataStruct& d) { return _ten(d.ask); })
        .def_property_readonly("bid_qty", [](const EMTMarketDataStruct& d) { return _ten_i(d.bid_qty); })
        .def_property_readonly("ask_qty", [](const EMTMarketDataStruct& d) { return _ten_i(d.ask_qty); });

    py::class_<QuoteSpi, PyQuoteSpi>(m, "QuoteSpi")
        .def(py::init<>());

    py::class_<QuoteApi>(m, "QuoteApi")
        .def_static("CreateQuoteApi", &QuoteApi::CreateQuoteApi, py::return_value_policy::reference)
        .def("RegisterSpi", &QuoteApi::RegisterSpi)
        .def("Login", &QuoteApi::Login)
        .def("Logout", &QuoteApi::Logout)
        .def("SubscribeMarketData", [](QuoteApi& self, py::list tickers, EMQ_EXCHANGE_TYPE ex) -> int {
            std::vector<std::string> keepalive;
            auto ptrs = _ticker_ptrs(tickers, keepalive);
            return self.SubscribeMarketData(ptrs.data(), static_cast<int>(ptrs.size()), ex);
        })
        .def("UnSubscribeMarketData", [](QuoteApi& self, py::list tickers, EMQ_EXCHANGE_TYPE ex) -> int {
            std::vector<std::string> keepalive;
            auto ptrs = _ticker_ptrs(tickers, keepalive);
            return self.UnSubscribeMarketData(ptrs.data(), static_cast<int>(ptrs.size()), ex);
        });
}
