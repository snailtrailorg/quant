# fina_indicator - 财务指标

> 实测: 2026-08-19 5000积分 | 更新: 按公告日增量 | 输入模式: per_symbol
> [官方文档](https://tushare.pro/document/2)

## 列结构（108 列，实测）

| # | 列名 | 类型 | 示例值 |
|---|---|---|---|
| 1 | `ts_code` | object |  |
| 2 | `ann_date` | object |  |
| 3 | `end_date` | object |  |
| 4 | `eps` | object |  |
| 5 | `dt_eps` | object |  |
| 6 | `total_revenue_ps` | object |  |
| 7 | `revenue_ps` | object |  |
| 8 | `capital_rese_ps` | object |  |
| 9 | `surplus_rese_ps` | object |  |
| 10 | `undist_profit_ps` | object |  |
| 11 | `extra_item` | object |  |
| 12 | `profit_dedt` | object |  |
| 13 | `gross_margin` | object |  |
| 14 | `current_ratio` | object |  |
| 15 | `quick_ratio` | object |  |
| 16 | `cash_ratio` | object |  |
| 17 | `ar_turn` | object |  |
| 18 | `ca_turn` | object |  |
| 19 | `fa_turn` | object |  |
| 20 | `assets_turn` | object |  |
| 21 | `op_income` | object |  |
| 22 | `ebit` | object |  |
| 23 | `ebitda` | object |  |
| 24 | `fcff` | object |  |
| 25 | `fcfe` | object |  |
| 26 | `current_exint` | object |  |
| 27 | `noncurrent_exint` | object |  |
| 28 | `interestdebt` | object |  |
| 29 | `netdebt` | object |  |
| 30 | `tangible_asset` | object |  |
| 31 | `working_capital` | object |  |
| 32 | `networking_capital` | object |  |
| 33 | `invest_capital` | object |  |
| 34 | `retained_earnings` | object |  |
| 35 | `diluted2_eps` | object |  |
| 36 | `bps` | object |  |
| 37 | `ocfps` | object |  |
| 38 | `retainedps` | object |  |
| 39 | `cfps` | object |  |
| 40 | `ebit_ps` | object |  |
| 41 | `fcff_ps` | object |  |
| 42 | `fcfe_ps` | object |  |
| 43 | `netprofit_margin` | object |  |
| 44 | `grossprofit_margin` | object |  |
| 45 | `cogs_of_sales` | object |  |
| 46 | `expense_of_sales` | object |  |
| 47 | `profit_to_gr` | object |  |
| 48 | `saleexp_to_gr` | object |  |
| 49 | `adminexp_of_gr` | object |  |
| 50 | `finaexp_of_gr` | object |  |
| 51 | `impai_ttm` | object |  |
| 52 | `gc_of_gr` | object |  |
| 53 | `op_of_gr` | object |  |
| 54 | `ebit_of_gr` | object |  |
| 55 | `roe` | object |  |
| 56 | `roe_waa` | object |  |
| 57 | `roe_dt` | object |  |
| 58 | `roa` | object |  |
| 59 | `npta` | object |  |
| 60 | `roic` | object |  |
| 61 | `roe_yearly` | object |  |
| 62 | `roa2_yearly` | object |  |
| 63 | `debt_to_assets` | object |  |
| 64 | `assets_to_eqt` | object |  |
| 65 | `dp_assets_to_eqt` | object |  |
| 66 | `ca_to_assets` | object |  |
| 67 | `nca_to_assets` | object |  |
| 68 | `tbassets_to_totalassets` | object |  |
| 69 | `int_to_talcap` | object |  |
| 70 | `eqt_to_talcapital` | object |  |
| 71 | `currentdebt_to_debt` | object |  |
| 72 | `longdeb_to_debt` | object |  |
| 73 | `ocf_to_shortdebt` | object |  |
| 74 | `debt_to_eqt` | object |  |
| 75 | `eqt_to_debt` | object |  |
| 76 | `eqt_to_interestdebt` | object |  |
| 77 | `tangibleasset_to_debt` | object |  |
| 78 | `tangasset_to_intdebt` | object |  |
| 79 | `tangibleasset_to_netdebt` | object |  |
| 80 | `ocf_to_debt` | object |  |
| 81 | `turn_days` | object |  |
| 82 | `roa_yearly` | object |  |
| 83 | `roa_dp` | object |  |
| 84 | `fixed_assets` | object |  |
| 85 | `profit_to_op` | object |  |
| 86 | `q_saleexp_to_gr` | object |  |
| 87 | `q_gc_to_gr` | object |  |
| 88 | `q_roe` | object |  |
| 89 | `q_dt_roe` | object |  |
| 90 | `q_npta` | object |  |
| 91 | `q_ocf_to_sales` | object |  |
| 92 | `basic_eps_yoy` | object |  |
| 93 | `dt_eps_yoy` | object |  |
| 94 | `cfps_yoy` | object |  |
| 95 | `op_yoy` | object |  |
| 96 | `ebt_yoy` | object |  |
| 97 | `netprofit_yoy` | object |  |
| 98 | `dt_netprofit_yoy` | object |  |
| 99 | `ocf_yoy` | object |  |
| 100 | `roe_yoy` | object |  |
| 101 | `bps_yoy` | object |  |
| 102 | `assets_yoy` | object |  |
| 103 | `eqt_yoy` | object |  |
| 104 | `tr_yoy` | object |  |
| 105 | `or_yoy` | object |  |
| 106 | `q_sales_yoy` | object |  |
| 107 | `q_op_qoq` | object |  |
| 108 | `equity_yoy` | object |  |

## 数据量
单次返回: 0 行

## 输入参数
```python
fina_indicator(ts_code='600000.SH', period='20260630')
```
