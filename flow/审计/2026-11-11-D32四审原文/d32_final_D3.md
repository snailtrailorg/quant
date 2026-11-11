# D32 终审复核 · 6fb57e7（只核 4 条）

| 项 | 结论 | 依据 |
|---|---|---|
| §9.5 租约 | 闭环 | 391-399 单语句 `INSERT…ON CONFLICT DO UPDATE … WHERE heartbeat < now()-45s RETURNING`；403 三态齐（有主 75 / 配置非法 78 / DB 不可用不建连退避 EX_TEMPFAIL，明禁 fail-open）；404 续租 0 行=被抢即退、异常=DB 错重试，不再混同；405 写明 MD 本机 Valkey、TD 跨机 DB slot 的分工（以账号归属唯一市场为前提） |
| §十一 配置 | 闭环 | 441 缺失/拼错/市场名非法=78，明禁默认全市场；442 渲染值+md5 落 `releases/<id>/deploy-market.txt`；444 订阅候选改按 `deploy_markets` 市场维筛，且 D26「账号级全量复制」口径明确不变 |
| §7.4 兜底链 | 闭环 | 285 第3级=最新价本身钳到涨跌停带内、不乘让价、市价档优先；286+288 第4级限定「无 tick/无最新价/无任何参照」并说明此时挂单必拒，与「禁止无规则→不动作」不再矛盾 |
| §7.5 close_policy | 闭环 | 296/301 明确 `has_limit_band`/`price_tick` 为**删**（非迁移，理由双源+0.001 与 0.01 冲突）；其余 `opponent_depth`/`aggression`/`max_order_qty` 迁 `trading_account.params`（provider 维） |

**结论：可进入 P1a 立项**
