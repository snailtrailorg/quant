# D32 终审（审官 B · 可实施性/迁移）· 063b1ea / 492 行

| 项 | 判定 | 依据（一句话） |
|---|---|---|
| 1 注册表域词表 | 闭环 | §十:410 定义 `DOMAIN_TRADING/RESEARCH`、manifest:329 `domain` 单值；§十:414-415 `get/pick` 均含 domain，§5.1:114 取键已改写，全文无 `caps(provider,impl)`。 |
| 2 两套租约 | 闭环 | §9.5:390 仅一套 `provider_session_slot` DB 租约，§十二:459 明文「不另设」；75=有主/78=配置非法 分工见 :391。 |
| 3 S7 等价可测 | 闭环 | §十四:482 增 legacy-compat 号段 + 时钟注入；client_id=`t{tid}:e{epoch}:c{n}`（adapters.py:825, main.py:331），冻结时钟即同序列——但等价前提须把 epoch 一并注入，建议在 S7 行点名。 |
| 4 数据侧删除批次 | 闭环 | §十二:453 新增 P7，四处逐一列名；依赖 A03 落地（排 P6 后）属排期风险，非文档缺口。 |
| 5 §3.1 两行分工 | 闭环 | §3.1:57-58 执行/实时/研究↔D32、数据拉取↔A03；全文已无 `role: pull`。 |
| 6 close_policy 去向 | 闭环 | §7.5:296 改「留 provider 维」（trading_account.params 或 manifest），:301 明说不拍扁；残留：正文称 provider×category，而 0175 仅 provider 级种子，category 级无归宿（P2 内消化）。 |
| 7 申报范围 | 闭环 | §7.1:217 字段/来源、:221 三列+合成顺序（symbol 覆盖>band_rules>§7.4）；§7.2 无 `price_range`，:221 明文不设。 |
| 8 号段作用域 | 闭环 | §5.1:126 / §9.5:389 唯一域含 terminal_id（由 role,tid 派生），同账号多任务取不同槽，不误锁。 |
| 9 MarketDataPort | 闭环 | §六:160-171 connect/set_context/unsubscribe/poll_supervise(trading_day) 五件齐。 |
| 10 停牌态两源 | 闭环 | §7.3:265 停牌表 + bar 新鲜度（超阈只告警），临停走 dynamic_rule，不依赖 hub 状态位。 |
| 11 平仓兜底链 | 闭环 | §7.4:283-288 四级：跌停线→对手价×aggression→最新价×(1-让价)→转人工；band_rules 缺行走 2/3 级，不会空转。 |
| 12 RuleSet 去 Caps | 闭环 | §5.1:128 caps 不进 RuleSet；RuleSet:253-257 仅 validate/reference_price/sellable/pre_trade_checks。 |
| 13 授权+资金门 | 闭环 | §5:106 `data_license` 落 trading_account.params、消费点=hub 订阅 diff；§7.3:269 资金门位置（check_order 内、validate 前）与口径（AccountSpec）明确。 |
| 14 唯一索引 | **未闭环** | :458 加了 market 维，但未删 0116:123 旧唯一 `ix_trading_account_account_key(provider,account_key)`——旧索引照旧拦跨市场同名，「避免误阻」目标不可达；且 provider 在键内，同句「阻止 xtp/emt 双行双开」也不成立。 |
| 15 部署配置 | 闭环 | §十一:427 /etc/quant/deploy.toml + host_vars、:429 订阅面本机范围、:430 跨机租约；注：写 /etc 需 root 通道（新 wrapper 或 bootstrap 装位），文中未写且分片无实施批。 |

- 结论：**可进入 P1a 立项**（14/15 闭环；14 不触 P1a 骨架，属 P3 双轨迁移前的必修）。
- 最小修法：① §十二:457 该条补「先 `DROP INDEX ix_trading_account_account_key` 再建 `(market,provider,account_key) WHERE account_key IS NOT NULL AND enabled`」；② 同句「阻止双行双开」改为「同 market+provider+account_key 单行」，跨 provider 同一资金账号改用校验而非唯一索引；③ §十四:482 括号补「时钟注入含 boot_epoch」。
