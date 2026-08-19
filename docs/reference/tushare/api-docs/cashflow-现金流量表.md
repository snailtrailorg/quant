# cashflow - 现金流量表

> 实测: 2026-08-19 5000积分 | 更新: 按公告日增量 | 输入模式: per_symbol
> [官方文档](https://tushare.pro/document/2)

## 列结构（97 列，实测）

| # | 列名 | 类型 | 示例值 |
|---|---|---|---|
| 1 | `ts_code` | object |  |
| 2 | `ann_date` | object |  |
| 3 | `f_ann_date` | object |  |
| 4 | `end_date` | object |  |
| 5 | `comp_type` | object |  |
| 6 | `report_type` | object |  |
| 7 | `end_type` | object |  |
| 8 | `net_profit` | object |  |
| 9 | `finan_exp` | object |  |
| 10 | `c_fr_sale_sg` | object |  |
| 11 | `recp_tax_rends` | object |  |
| 12 | `n_depos_incr_fi` | object |  |
| 13 | `n_incr_loans_cb` | object |  |
| 14 | `n_inc_borr_oth_fi` | object |  |
| 15 | `prem_fr_orig_contr` | object |  |
| 16 | `n_incr_insured_dep` | object |  |
| 17 | `n_reinsur_prem` | object |  |
| 18 | `n_incr_disp_tfa` | object |  |
| 19 | `ifc_cash_incr` | object |  |
| 20 | `n_incr_disp_faas` | object |  |
| 21 | `n_incr_loans_oth_bank` | object |  |
| 22 | `n_cap_incr_repur` | object |  |
| 23 | `c_fr_oth_operate_a` | object |  |
| 24 | `c_inf_fr_operate_a` | object |  |
| 25 | `c_paid_goods_s` | object |  |
| 26 | `c_paid_to_for_empl` | object |  |
| 27 | `c_paid_for_taxes` | object |  |
| 28 | `n_incr_clt_loan_adv` | object |  |
| 29 | `n_incr_dep_cbob` | object |  |
| 30 | `c_pay_claims_orig_inco` | object |  |
| 31 | `pay_handling_chrg` | object |  |
| 32 | `pay_comm_insur_plcy` | object |  |
| 33 | `oth_cash_pay_oper_act` | object |  |
| 34 | `st_cash_out_act` | object |  |
| 35 | `n_cashflow_act` | object |  |
| 36 | `oth_recp_ral_inv_act` | object |  |
| 37 | `c_disp_withdrwl_invest` | object |  |
| 38 | `c_recp_return_invest` | object |  |
| 39 | `n_recp_disp_fiolta` | object |  |
| 40 | `n_recp_disp_sobu` | object |  |
| 41 | `stot_inflows_inv_act` | object |  |
| 42 | `c_pay_acq_const_fiolta` | object |  |
| 43 | `c_paid_invest` | object |  |
| 44 | `n_disp_subs_oth_biz` | object |  |
| 45 | `oth_pay_ral_inv_act` | object |  |
| 46 | `n_incr_pledge_loan` | object |  |
| 47 | `stot_out_inv_act` | object |  |
| 48 | `n_cashflow_inv_act` | object |  |
| 49 | `c_recp_borrow` | object |  |
| 50 | `proc_issue_bonds` | object |  |
| 51 | `oth_cash_recp_ral_fnc_act` | object |  |
| 52 | `stot_cash_in_fnc_act` | object |  |
| 53 | `free_cashflow` | object |  |
| 54 | `c_prepay_amt_borr` | object |  |
| 55 | `c_pay_dist_dpcp_int_exp` | object |  |
| 56 | `incl_dvd_profit_paid_sc_ms` | object |  |
| 57 | `oth_cashpay_ral_fnc_act` | object |  |
| 58 | `stot_cashout_fnc_act` | object |  |
| 59 | `n_cash_flows_fnc_act` | object |  |
| 60 | `eff_fx_flu_cash` | object |  |
| 61 | `n_incr_cash_cash_equ` | object |  |
| 62 | `c_cash_equ_beg_period` | object |  |
| 63 | `c_cash_equ_end_period` | object |  |
| 64 | `c_recp_cap_contrib` | object |  |
| 65 | `incl_cash_rec_saims` | object |  |
| 66 | `uncon_invest_loss` | object |  |
| 67 | `prov_depr_assets` | object |  |
| 68 | `depr_fa_coga_dpba` | object |  |
| 69 | `amort_intang_assets` | object |  |
| 70 | `lt_amort_deferred_exp` | object |  |
| 71 | `decr_deferred_exp` | object |  |
| 72 | `incr_acc_exp` | object |  |
| 73 | `loss_disp_fiolta` | object |  |
| 74 | `loss_scr_fa` | object |  |
| 75 | `loss_fv_chg` | object |  |
| 76 | `invest_loss` | object |  |
| 77 | `decr_def_inc_tax_assets` | object |  |
| 78 | `incr_def_inc_tax_liab` | object |  |
| 79 | `decr_inventories` | object |  |
| 80 | `decr_oper_payable` | object |  |
| 81 | `incr_oper_payable` | object |  |
| 82 | `others` | object |  |
| 83 | `im_net_cashflow_oper_act` | object |  |
| 84 | `conv_debt_into_cap` | object |  |
| 85 | `conv_copbonds_due_within_1y` | object |  |
| 86 | `fa_fnc_leases` | object |  |
| 87 | `im_n_incr_cash_equ` | object |  |
| 88 | `net_dism_capital_add` | object |  |
| 89 | `net_cash_rece_sec` | object |  |
| 90 | `credit_impa_loss` | object |  |
| 91 | `use_right_asset_dep` | object |  |
| 92 | `oth_loss_asset` | object |  |
| 93 | `end_bal_cash` | object |  |
| 94 | `beg_bal_cash` | object |  |
| 95 | `end_bal_cash_equ` | object |  |
| 96 | `beg_bal_cash_equ` | object |  |
| 97 | `update_flag` | object |  |

## 数据量
单次返回: 0 行

## 输入参数
```python
cashflow(ts_code='600000.SH', period='20260630', report_type='1')
```
