# income - 利润表

> 实测: 2026-08-19 5000积分 | 更新: 按公告日增量 | 输入模式: per_symbol
> [官方文档](https://tushare.pro/document/2)

## 列结构（85 列，实测）

| # | 列名 | 类型 | 示例值 |
|---|---|---|---|
| 1 | `ts_code` | object |  |
| 2 | `ann_date` | object |  |
| 3 | `f_ann_date` | object |  |
| 4 | `end_date` | object |  |
| 5 | `report_type` | object |  |
| 6 | `comp_type` | object |  |
| 7 | `end_type` | object |  |
| 8 | `basic_eps` | object |  |
| 9 | `diluted_eps` | object |  |
| 10 | `total_revenue` | object |  |
| 11 | `revenue` | object |  |
| 12 | `int_income` | object |  |
| 13 | `prem_earned` | object |  |
| 14 | `comm_income` | object |  |
| 15 | `n_commis_income` | object |  |
| 16 | `n_oth_income` | object |  |
| 17 | `n_oth_b_income` | object |  |
| 18 | `prem_income` | object |  |
| 19 | `out_prem` | object |  |
| 20 | `une_prem_reser` | object |  |
| 21 | `reins_income` | object |  |
| 22 | `n_sec_tb_income` | object |  |
| 23 | `n_sec_uw_income` | object |  |
| 24 | `n_asset_mg_income` | object |  |
| 25 | `oth_b_income` | object |  |
| 26 | `fv_value_chg_gain` | object |  |
| 27 | `invest_income` | object |  |
| 28 | `ass_invest_income` | object |  |
| 29 | `forex_gain` | object |  |
| 30 | `total_cogs` | object |  |
| 31 | `oper_cost` | object |  |
| 32 | `int_exp` | object |  |
| 33 | `comm_exp` | object |  |
| 34 | `biz_tax_surchg` | object |  |
| 35 | `sell_exp` | object |  |
| 36 | `admin_exp` | object |  |
| 37 | `fin_exp` | object |  |
| 38 | `assets_impair_loss` | object |  |
| 39 | `prem_refund` | object |  |
| 40 | `compens_payout` | object |  |
| 41 | `reser_insur_liab` | object |  |
| 42 | `div_payt` | object |  |
| 43 | `reins_exp` | object |  |
| 44 | `oper_exp` | object |  |
| 45 | `compens_payout_refu` | object |  |
| 46 | `insur_reser_refu` | object |  |
| 47 | `reins_cost_refund` | object |  |
| 48 | `other_bus_cost` | object |  |
| 49 | `operate_profit` | object |  |
| 50 | `non_oper_income` | object |  |
| 51 | `non_oper_exp` | object |  |
| 52 | `nca_disploss` | object |  |
| 53 | `total_profit` | object |  |
| 54 | `income_tax` | object |  |
| 55 | `n_income` | object |  |
| 56 | `n_income_attr_p` | object |  |
| 57 | `minority_gain` | object |  |
| 58 | `oth_compr_income` | object |  |
| 59 | `t_compr_income` | object |  |
| 60 | `compr_inc_attr_p` | object |  |
| 61 | `compr_inc_attr_m_s` | object |  |
| 62 | `ebit` | object |  |
| 63 | `ebitda` | object |  |
| 64 | `insurance_exp` | object |  |
| 65 | `undist_profit` | object |  |
| 66 | `distable_profit` | object |  |
| 67 | `rd_exp` | object |  |
| 68 | `fin_exp_int_exp` | object |  |
| 69 | `fin_exp_int_inc` | object |  |
| 70 | `transfer_surplus_rese` | object |  |
| 71 | `transfer_housing_imprest` | object |  |
| 72 | `transfer_oth` | object |  |
| 73 | `adj_lossgain` | object |  |
| 74 | `withdra_legal_surplus` | object |  |
| 75 | `withdra_legal_pubfund` | object |  |
| 76 | `withdra_biz_devfund` | object |  |
| 77 | `withdra_rese_fund` | object |  |
| 78 | `withdra_oth_ersu` | object |  |
| 79 | `workers_welfare` | object |  |
| 80 | `distr_profit_shrhder` | object |  |
| 81 | `prfshare_payable_dvd` | object |  |
| 82 | `comshare_payable_dvd` | object |  |
| 83 | `capit_comstock_div` | object |  |
| 84 | `continued_net_profit` | object |  |
| 85 | `update_flag` | object |  |

## 数据量
单次返回: 0 行

## 输入参数
```python
income(ts_code='600000.SH', period='20260630', report_type='1')
```
