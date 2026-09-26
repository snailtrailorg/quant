// 批 63 Phase B P4：EMT 极速柜台交易 pybind11 绑定（TraderApi/TraderSpi）。
// 命名空间 EMT::API（行情是 EMQ::API）；TraderApi 单例+析构 protected——
// nodelete holder（B 审实测：默认 holder 编译炸，return_value_policy::reference
// 救不了 class 注册期实例化）；C++ 默认参不透传——默认参/指针参方法一律 lambda 包装。
#include <pybind11/pybind11.h>
#include <cstring>
#include <string>
#include "bind_common.h"
#include "emt_trader_api.h"

namespace py = pybind11;
using namespace EMT::API;

namespace {

class PyTraderSpi : public TraderSpi {
public:
    using TraderSpi::TraderSpi;

    void OnConnected() override {
        EMT_OVERRIDE_GUARDED(TraderSpi, "OnConnected");
    }
    void OnDisconnected(int reason) override {
        EMT_OVERRIDE_GUARDED(TraderSpi, "OnDisconnected", reason);
    }
    void OnError(EMTRI *error_info) override {
        EMT_OVERRIDE_GUARDED(TraderSpi, "OnError", error_info);
    }
    void OnOrderEvent(EMTOrderInfo *order_info, EMTRI *error_info, uint64_t session_id) override {
        EMT_OVERRIDE_GUARDED(TraderSpi, "OnOrderEvent", order_info, error_info, session_id);
    }
    void OnTradeEvent(EMTTradeReport *trade_info, uint64_t session_id) override {
        EMT_OVERRIDE_GUARDED(TraderSpi, "OnTradeEvent", trade_info, session_id);
    }
    void OnCancelOrderError(EMTOrderCancelInfo *cancel_info, EMTRI *error_info, uint64_t session_id) override {
        EMT_OVERRIDE_GUARDED(TraderSpi, "OnCancelOrderError", cancel_info, error_info, session_id);
    }
    // UnfinishedOrders 的响应同走 OnQueryOrder（SDK 无独立回调）；QueryTrades 不绑
    // （成交补录靠公共流 RESTART 重传——任务文件 P4 规格 N1 钉死）
    void OnQueryOrder(EMTQueryOrderRsp *order_info, EMTRI *error_info, int request_id, bool is_last, uint64_t session_id) override {
        EMT_OVERRIDE_GUARDED(TraderSpi, "OnQueryOrder", order_info, error_info, request_id, is_last, session_id);
    }
    void OnQueryPosition(EMTQueryStkPositionRsp *position, EMTRI *error_info, int request_id, bool is_last, uint64_t session_id) override {
        EMT_OVERRIDE_GUARDED(TraderSpi, "OnQueryPosition", position, error_info, request_id, is_last, session_id);
    }
    void OnQueryAsset(EMTQueryAssetRsp *asset, EMTRI *error_info, int request_id, bool is_last, uint64_t session_id) override {
        EMT_OVERRIDE_GUARDED(TraderSpi, "OnQueryAsset", asset, error_info, request_id, is_last, session_id);
    }
};

std::string _s(const char* c) { return c ? std::string(c) : std::string(); }

}  // namespace

PYBIND11_MODULE(emt_trader_api, m) {
    m.doc() = "东方财富 EMT 极速柜台 TraderApi 绑定（批 63 Phase B P4）";

    py::enum_<EMT_LOG_LEVEL>(m, "EMT_LOG_LEVEL")
        .value("TRACE", EMT_LOG_LEVEL_TRACE)
        .value("DEBUG", EMT_LOG_LEVEL_DEBUG)
        .value("INFO", EMT_LOG_LEVEL_INFO)
        .value("WARNING", EMT_LOG_LEVEL_WARNING)
        .value("ERROR", EMT_LOG_LEVEL_ERROR)
        .value("FATAL", EMT_LOG_LEVEL_FATAL)
        .export_values();

    py::enum_<EMT_MARKET_TYPE>(m, "EMT_MARKET_TYPE")
        .value("INIT", EMT_MKT_INIT)
        .value("SZ_A", EMT_MKT_SZ_A)      // 交易侧 1=SZ/2=SH——与行情 exchange_id 对调（陷阱）
        .value("SH_A", EMT_MKT_SH_A)
        .value("BJ_A", EMT_MKT_BJ_A)
        .export_values();

    py::enum_<EMT_PRICE_TYPE>(m, "EMT_PRICE_TYPE")
        .value("LIMIT", EMT_PRICE_LIMIT)
        .value("BEST5_OR_LIMIT", EMT_PRICE_BEST5_OR_LIMIT)     // 沪专
        .value("BEST_OR_CANCEL", EMT_PRICE_BEST_OR_CANCEL)     // 深专
        .value("BEST5_OR_CANCEL", EMT_PRICE_BEST5_OR_CANCEL)   // 沪深通用（市价缺省钉此）
        .value("FORWARD_BEST", EMT_PRICE_FORWARD_BEST)
        .value("REVERSE_BEST_LIMIT", EMT_PRICE_REVERSE_BEST_LIMIT)
        .export_values();

    py::enum_<EMT_TE_RESUME_TYPE>(m, "EMT_TE_RESUME_TYPE")
        .value("RESTART", EMT_TERT_RESTART)   // 当日全量重传（钉此——成交补录依赖）
        .value("RESUME", EMT_TERT_RESUME)     // 保留字段暂未支持
        .value("QUICK", EMT_TERT_QUICK)
        .export_values();

    // EMT_SIDE_TYPE / EMT_BUSINESS_TYPE_CASH 是 #define 数值——绑常量；
    // EMT_ORDER_STATUS_TYPE 是真 enum——绑枚举（快审 N3 符号：PARTTRADEDNOTQUEUEING 非 NOTRADE）
    m.attr("EMT_SIDE_BUY") = EMT_SIDE_BUY;
    m.attr("EMT_SIDE_SELL") = EMT_SIDE_SELL;
    py::enum_<EMT_ORDER_STATUS_TYPE>(m, "EMT_ORDER_STATUS_TYPE")
        .value("INIT", EMT_ORDER_STATUS_INIT)
        .value("ALLTRADED", EMT_ORDER_STATUS_ALLTRADED)
        .value("PARTTRADEDQUEUEING", EMT_ORDER_STATUS_PARTTRADEDQUEUEING)
        .value("PARTTRADEDNOTQUEUEING", EMT_ORDER_STATUS_PARTTRADEDNOTQUEUEING)   // 部撤
        .value("NOTRADEQUEUEING", EMT_ORDER_STATUS_NOTRADEQUEUEING)
        .value("CANCELED", EMT_ORDER_STATUS_CANCELED)
        .value("REJECTED", EMT_ORDER_STATUS_REJECTED)
        .value("UNKNOWN", EMT_ORDER_STATUS_UNKNOWN)
        .export_values();
    m.attr("EMT_BUSINESS_TYPE_CASH") = EMT_BUSINESS_TYPE_CASH;

    py::class_<EMTRI>(m, "EMTRI")
        .def_readonly("error_id", &EMTRI::error_id)
        .def_property_readonly("error_msg", [](const EMTRI& r) { return _s(r.error_msg); });

    // 下单请求（可写；ticker strncpy 截断辅助；order_emt_id/order_client_id 由 Python 填）
    py::class_<EMTOrderInsertInfo>(m, "EMTOrderInsertInfo")
        .def(py::init<>())
        .def_readwrite("order_client_id", &EMTOrderInsertInfo::order_client_id)
        .def_property("ticker",
                      [](const EMTOrderInsertInfo& o) { return _s(o.ticker); },
                      [](EMTOrderInsertInfo& o, const std::string& t) {
                          std::strncpy(o.ticker, t.c_str(), sizeof(o.ticker) - 1);
                          o.ticker[sizeof(o.ticker) - 1] = '\0';
                      })
        .def_readwrite("market", &EMTOrderInsertInfo::market)
        .def_readwrite("price", &EMTOrderInsertInfo::price)
        .def_readwrite("quantity", &EMTOrderInsertInfo::quantity)
        .def_readwrite("price_type", &EMTOrderInsertInfo::price_type)
        .def_readwrite("side", &EMTOrderInsertInfo::side)
        .def_readwrite("business_type", &EMTOrderInsertInfo::business_type);

    py::class_<EMTOrderCancelInfo>(m, "EMTOrderCancelInfo")
        .def_readonly("order_cancel_emt_id", &EMTOrderCancelInfo::order_cancel_emt_id)
        .def_readonly("order_emt_id", &EMTOrderCancelInfo::order_emt_id);

    py::class_<EMTOrderInfo>(m, "EMTOrderInfo")
        .def_readonly("order_emt_id", &EMTOrderInfo::order_emt_id)
        .def_readonly("order_client_id", &EMTOrderInfo::order_client_id)
        .def_property_readonly("ticker", [](const EMTOrderInfo& o) { return _s(o.ticker); })
        .def_readonly("market", &EMTOrderInfo::market)
        .def_readonly("price", &EMTOrderInfo::price)
        .def_readonly("quantity", &EMTOrderInfo::quantity)
        .def_readonly("price_type", &EMTOrderInfo::price_type)
        .def_readonly("side", &EMTOrderInfo::side)
        .def_readonly("qty_traded", &EMTOrderInfo::qty_traded)
        .def_readonly("qty_left", &EMTOrderInfo::qty_left)
        .def_readonly("insert_time", &EMTOrderInfo::insert_time)
        .def_readonly("update_time", &EMTOrderInfo::update_time)
        .def_readonly("cancel_time", &EMTOrderInfo::cancel_time)
        .def_readonly("trade_amount", &EMTOrderInfo::trade_amount)
        .def_property_readonly("order_local_id", [](const EMTOrderInfo& o) { return _s(o.order_local_id); })
        .def_readonly("order_status", &EMTOrderInfo::order_status);

    py::class_<EMTTradeReport>(m, "EMTTradeReport")
        .def_readonly("order_emt_id", &EMTTradeReport::order_emt_id)
        .def_readonly("order_client_id", &EMTTradeReport::order_client_id)
        .def_property_readonly("ticker", [](const EMTTradeReport& t) { return _s(t.ticker); })
        .def_readonly("market", &EMTTradeReport::market)
        .def_readonly("price", &EMTTradeReport::price)
        .def_readonly("quantity", &EMTTradeReport::quantity)   // 本次非累计
        .def_readonly("trade_time", &EMTTradeReport::trade_time)
        .def_readonly("trade_amount", &EMTTradeReport::trade_amount)
        .def_property_readonly("exec_id", [](const EMTTradeReport& t) { return _s(t.exec_id); })
        .def_readonly("report_index", &EMTTradeReport::report_index)
        .def_property_readonly("order_exch_id", [](const EMTTradeReport& t) { return _s(t.order_exch_id); })
        .def_readonly("side", &EMTTradeReport::side);

    py::class_<EMTQueryStkPositionRsp>(m, "EMTQueryStkPositionRsp")
        .def_property_readonly("ticker", [](const EMTQueryStkPositionRsp& p) { return _s(p.ticker); })
        .def_readonly("market", &EMTQueryStkPositionRsp::market)
        .def_readonly("total_qty", &EMTQueryStkPositionRsp::total_qty)
        .def_readonly("sellable_qty", &EMTQueryStkPositionRsp::sellable_qty)
        .def_readonly("avg_price", &EMTQueryStkPositionRsp::avg_price)
        .def_readonly("yesterday_position", &EMTQueryStkPositionRsp::yesterday_position)
        .def_readonly("unrealized_pnl", &EMTQueryStkPositionRsp::unrealized_pnl);  // 保留字段恒 0

    py::class_<EMTQueryAssetRsp>(m, "EMTQueryAssetRsp")
        .def_readonly("total_asset", &EMTQueryAssetRsp::total_asset)
        .def_readonly("buying_power", &EMTQueryAssetRsp::buying_power)
        .def_readonly("security_asset", &EMTQueryAssetRsp::security_asset);

    py::class_<TraderSpi, PyTraderSpi>(m, "TraderSpi")
        .def(py::init<>());

    // 单例+protected 析构：nodelete holder（B 实测）+def_static reference policy
    // （Release() 是唯一合法销毁通道——Python 侧禁止 del/GC 触发 delete）
    py::class_<TraderApi, std::unique_ptr<TraderApi, py::nodelete>>(m, "TraderApi")
        .def_static("CreateTraderApi",
                    [](uint8_t client_id, const std::string& save_path, EMT_LOG_LEVEL level) {
                        return TraderApi::CreateTraderApi(client_id, save_path.c_str(), level);
                    },
                    py::arg("client_id"), py::arg("save_path"),
                    py::arg("log_level") = EMT_LOG_LEVEL_DEBUG,
                    py::return_value_policy::reference)
        .def("RegisterSpi", &TraderApi::RegisterSpi, py::keep_alive<1, 2>())
        .def("SubscribePublicTopic", &TraderApi::SubscribePublicTopic)
        .def("Login",
             [](TraderApi& self, const std::string& ip, int port, const std::string& user,
                const std::string& password, int sock_type) -> uint64_t {
                 // 默认参不透传：local_ip/terminal_info 固定 nullptr（lambda 包装）
                 return self.Login(ip.c_str(), port, user.c_str(), password.c_str(),
                                   static_cast<EMT_PROTOCOL_TYPE>(sock_type), nullptr, nullptr);
             },
             py::call_guard<py::gil_scoped_release>())   // 同步阻塞可数秒——释放 GIL
        .def("Logout", &TraderApi::Logout)
        .def("Release", &TraderApi::Release)
        .def("InsertOrder", &TraderApi::InsertOrder)
        .def("CancelOrder", &TraderApi::CancelOrder)
        .def("QueryUnfinishedOrders", &TraderApi::QueryUnfinishedOrders)
        .def("QueryPosition",
             [](TraderApi& self, py::object ticker, uint64_t session_id, int request_id) -> int {
                 // 默认参 market 不透传+ticker None→NULL（查全部）；按标的查须 market 匹配
                 // （SDK 陷阱：不匹配可能查不到）——Python 侧只走全市场查询。
                 // 局部 string 存活覆盖同步调用（strdup 泄漏修，代码审 P2-5）
                 std::string keep = ticker.is_none() ? std::string()
                                                     : py::cast<std::string>(ticker);
                 return self.QueryPosition(keep.empty() ? nullptr : keep.c_str(),
                                           session_id, request_id);
             })
        .def("QueryAsset", &TraderApi::QueryAsset)
        .def("GetApiLastError", &TraderApi::GetApiLastError,
             py::return_value_policy::reference)
        .def("GetTradingDay", [](TraderApi& self) { return _s(self.GetTradingDay()); })
        .def("GetClientIDByEMTID", &TraderApi::GetClientIDByEMTID);
}
