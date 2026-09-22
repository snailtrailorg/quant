// 批 63 Phase B：EMQ 极速行情 pybind11 绑定（P1 最小面——编译链验证）。
// 命名空间 EMQ::API；QuoteApi=纯虚接口（CreateQuoteApi 工厂），QuoteSpi=回调基类。
#include <pybind11/pybind11.h>
#include "quote_api.h"
#include "emt_quote_struct.h"
#include "emt_quote_data_type.h"

namespace py = pybind11;
using namespace EMQ::API;

namespace {

// QuoteSpi trampoline：让 Python 侧继承并覆写回调（OnDepthMarketData/OnError）。
class PyQuoteSpi : public QuoteSpi {
public:
    using QuoteSpi::QuoteSpi;

    void OnDepthMarketData(EMTMarketDataStruct* market_data, int64_t bid1_qty[], int32_t bid1_count,
                           int32_t max_bid1_count, int64_t ask1_qty[], int32_t ask1_count,
                           int32_t max_ask1_count) override {
        PYBIND11_OVERRIDE(void, QuoteSpi, OnDepthMarketData, market_data, bid1_qty, bid1_count,
                          max_bid1_count, ask1_qty, ask1_count, max_ask1_count);
    }

    void OnError(const EMTRspInfoStruct* error_info) override {
        PYBIND11_OVERRIDE(void, QuoteSpi, OnError, error_info);
    }
};

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

    py::class_<QuoteSpi, PyQuoteSpi>(m, "QuoteSpi")
        .def(py::init<>());

    py::class_<QuoteApi>(m, "QuoteApi")
        .def_static("CreateQuoteApi", &QuoteApi::CreateQuoteApi, py::return_value_policy::reference)
        .def("RegisterSpi", &QuoteApi::RegisterSpi)
        .def("Login", &QuoteApi::Login)
        .def("Logout", &QuoteApi::Logout);
}
