#!/usr/bin/env python3
"""批16 列宽数据评估（2026-09-12 用户裁定：路线一+设计时数据评估权重）。

对批16 清单各表的数据源测各列 char_length 分布（p50/p90/max+样本数），
换算建议 min-width。本地 dev 库跑（QUANT_DB_URL 同源），只读。

用法：python3 scripts/eval-col-widths.py [组名]
  组名缺省=全部；可选 research/live/risk/ops
产出：stdout markdown 表（落 docs/任务/批16-列宽数据评估.md 由人工粘贴）
"""
import os
import sys

import psycopg

DB = os.environ.get("QUANT_DB_URL", "postgresql://quant@127.0.0.1:5432/quant")

# 映射：视图 → 数据源（表/视图 + 列名=前端列的实际数据列）。
# 只列主体表；弹窗/子表随页补。列名给 SQL 标识符（与库一致），前端展示名在批16 清单。
SOURCES = {
    "research": [
        ("选股器.A股", "screen_astock", ["ts_code", "name", "close", "pe", "pb", "turnover", "total_mv", "pe_ttm"]),
        ("选股器.转债", "screen_cb", ["ts_code", "name", "stk_code", "stk_name", "bond_close", "double_low", "premium_pct", "conv_price", "maturity_date", "stk_close"]),
        ("选股器.ETF", "screen_etf", ["ts_code", "name", "fund_type", "fund_scale", "management_fee", "tracking_error", "management", "invest_type"]),
        ("股票池.主表", "pools", ["id", "name", "category", "description", "minute_history_start"]),  # symbols数/minute_count=API 聚合列，用缺省 80/100
        ("股票池.标的", "pool_symbols", ["symbol", "last_ts", "covered"]),
        ("股票池.攒数据", "minute_symbols", ["symbol", "source", "last_ts"]),
        ("因子库", "factor_def", ["name", "category", "type", "needs_history", "refs", "description", "params"]),
        ("策略", "strategy_config", ["id", "name", "type", "enabled", "backtest_verified", "adapter", "risk"]),
        ("回测", "backtest_runs", ["id", "strategy_name", "total_return_pct", "max_drawdown_pct", "sharpe", "start_date", "end_date", "fail_reason", "status", "mode", "symbol_count", "created_at"]),
        ("研判", "astock_analysis", ["symbol", "score", "rating", "support", "resistance", "conclusion"]),
    ],
    "live": [
        ("实盘任务", "live_task", ["id", "name", "strategy_id", "symbol", "md_mode", "status", "account_id", "initial_capital", "created_at", "owner_username"]),
        ("交易台.持仓", "position_snapshot", ["symbol", "direction", "volume", "frozen", "cost_price", "pnl", "pnl_pct", "market_value"]),
        ("交易台.委托", "order_log", ["ts", "symbol", "action", "volume", "price", "status", "client_order_id", "error", "strategy_id"]),
        ("回测详情.标的", "backtest_symbols", ["symbol", "status", "total_return_pct", "shpe", "win_rate", "max_drawdown_pct", "volatility", "sortino_ratio", "alpha", "beta", "information_ratio", "span_days"]),
    ],
    "risk": [
        ("风控日志", "risk_log", ["ts", "action", "symbol", "rule", "detail", "severity"]),
        ("对账差异", "reconcile_issue", ["symbol", "issue_type", "broker_qty", "derived_qty", "first_seen", "status", "updated_at", "handled_by", "note", "exempt_qty", "exempt_until"]),
        ("风控规则", "risk_rules", ["name", "type", "params", "enabled", "updated_at"]),
        ("实盘分项开关", "live_trading_config", ["market", "label", "enabled", "updated_at"]),
    ],
    "ops": [
        ("用户", "users", ["id", "username", "role", "enabled", "created_at", "last_login_at", "nickname", "email"]),
        ("用户组", "user_group", ["name", "description", "builtin", "user_count"]),
        ("同步配置", "sync_config", ["name", "data_type", "provider", "mode", "schedule", "trade_day_filter", "status", "last_sync_count", "last_sync_ts", "enabled", "tushare_api", "pg_table", "last_sync_date", "description"]),
        ("同步日志", "sync_log", ["sync_id", "ts", "mode", "rows_pulled", "rows_saved", "duration_ms", "status"]),
        ("完整性", "data_integrity", ["symbol", "local_count", "first", "last", "expected", "status"]),
        ("调度任务", "tasks", ["id", "name", "type", "status", "progress", "last_heartbeat", "trigger_type", "trigger_user", "error_message", "start_time", "end_time"]),
        ("券商通道", "broker_config", ["provider", "name", "has_credential", "enabled", "updated_at"]),
        ("LLM模型", "llm_model_config", ["id", "name", "provider", "model", "key_hint", "priority", "enabled", "base_url", "context_window", "max_input_tokens", "max_output_tokens"]),
        ("LLM预算", "llm_budget", ["provider", "daily_token_limit", "alert_threshold_pct", "enabled", "monthly_cost_limit", "updated_at"]),
        ("数据源", "data_source_config", ["provider", "name", "has_credential", "usage_limit", "enabled", "updated_at"]),
        ("交易账户", "accounts", ["id", "name", "exchange", "api_key_hint", "status", "created_at"]),
        ("健康事件", "health_event", ["ts", "severity", "component", "rule_id", "detail"]),
        ("通知", "notifications", ["level", "category", "title", "body", "created_at", "source_ref", "code"]),
        ("发件箱", "email_outbox", ["status", "attempts", "to_addr", "subject", "next_attempt_at", "last_error"]),
        ("审计", "audit_log", ["ts", "actor", "action", "target", "detail"]),
        ("我的IM通道", "im_bot_config", ["provider", "name", "status", "description", "updated_at"]),
        ("告警订阅", "alert_channel_sub", ["channel", "target", "categories", "min_level", "enabled"]),
    ],
}

# 内容宽 → px 换算：中文字符≈15px、ASCII≈8px（13px 字号近似）。粗略但方向对：
# 建议宽 = max(表头估宽, p90 内容宽) + 24 padding；数字/枚举类列封顶不超 140。
def est_px(p90: float, mx: float, header_cn: int = 4) -> int:
    cn = int(p90 * 15) if p90 else 0
    asc = int(p90 * 8)
    content = max(cn if p90 and p90 <= 8 else asc, 0)   # 短值按中文估、长值按 ASCII 估（保守取窄）
    w = max(header_cn * 15 + 20, content + 24)
    return min(w, 360)


def main(group: str | None) -> None:
    groups = [group] if group else list(SOURCES)
    with psycopg.connect(DB, autocommit=True) as conn:
        for g in groups:
            print(f"\n## {g} 组\n")
            print("| 表 | 列 | 样本 | p50 | p90 | max | 建议 min-width |")
            print("|---|---|---|---|---|---|---|")
            for label, table, cols in SOURCES[g]:
                exists = conn.execute(
                    "SELECT to_regclass(%s) IS NOT NULL", (f"public.{table}",)).fetchone()[0]
                if not exists:
                    print(f"| {label} | （表 {table} 不存在——视图级数据源，改由 API 层评估） | | | | | |")
                    continue
                for col in cols:
                    try:
                        row = conn.execute(
                            f"SELECT count(*), percentile_cont(0.5) WITHIN GROUP (ORDER BY char_length({col}::text)), "
                            f"percentile_cont(0.9) WITHIN GROUP (ORDER BY char_length({col}::text)), "
                            f"max(char_length({col}::text)) FROM {table} WHERE {col} IS NOT NULL").fetchone()
                    except Exception:
                        print(f"| {label} | {col} | （列不存在） | | | | |")
                        continue
                    n, p50, p90, mx = row
                    if not n:
                        print(f"| {label} | {col} | 0 | | | | （无数据——用类型缺省） |")
                        continue
                    w = est_px(float(p90 or 0), float(mx or 0))
                    print(f"| {label} | {col} | {n} | {int(p50 or 0)} | {int(p90 or 0)} | {int(mx or 0)} | {w} |")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else None)
