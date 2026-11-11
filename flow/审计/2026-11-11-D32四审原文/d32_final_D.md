# D32 终审红队 · 063b1ea（假设实施一年后回看，只核 6 主线）

**1. F2 号段** 结论：闭环（残留 P2）。`task_ctx.tid`→`acquire(account,provider,role,tid)`（5.1:112-113）、slot 携带 `{tid,terminal_id}`（9.3:366-371）、前缀 `t{tid}:e{epoch}`（9.5:393）已补齐；同账号多任务并行恢复，per-(account,role) 自锁已消。
- 残留：`(tid-1)%98+2` 下并发任务号相差 98 即同号 → 后者永远 75（前者活着不释放）。场景：任务 #5 与 #103 同账号 → #103 无限 75/重启，日志只显示"槽位被占"。修：启动即判断号耗尽/冲突 → 78（非 75），并写明单账号并发 TD 上限 98。
- 残留：租约仅约束新路径；P3/P4 期 legacy worker（不写 `provider_session_slot`）与他机同名单元仍可同号（TD 被 XTP 撞回，MD 无阻）。修：legacy build 前也抢同一 slot，或明文禁止跨机同一任务期望。

**2. N1 租约跨机** 结论：仍有洞（P0，契约缺算法）。DB 表+UNIQUE 是对的真源，但"互斥成立"只在原子实现下成立。
- 洞 1 抢占 TOCTOU：未写 acquire 语句。若"读到 stale→DELETE→INSERT"，两机同抢 stale 槽可双双成功 → 双会话。修：单条 `INSERT…ON CONFLICT DO UPDATE … WHERE heartbeat<now()-TTL RETURNING`，续租 CAS 校验 holder+boot_epoch。
- 洞 2 DB 故障是第三态，9.5 只写 75/78：fail-open 裸跑=撞号，fail-closed=全部 worker 起不来/中途退，二者均未定。场景：PG 抖动 60s，所有 worker 同时断连退出，持仓无人管。修：明文"无有效租约绝不建连"；启动期 DB 错=退避重试，绝不裸跑。
- 洞 3 续租把"DB 错"与"被抢占"混为"连续 3 次失败即退出"；且 TTL 若同为 45s，旧进程断连时刻=新进程可接管时刻 → 重叠窗。现 hub 对网络异常尚容忍一轮（md_hub/main.py:366），D32 反而更激进。修：TTL≥2×自栅时限（60s/40s），CAS=0 立即退，DB 错只告警；超 TTL 前必先断开。
- 洞 4 hub 互斥仍是本机 Valkey 租约（md_hub/parts.py:216，§3.1:65"不变"），DB slot 只覆盖 TD → 两机同市场时 hub 双开=双份 bar 流（见 N4 触发路径）。

**3. N2 订阅面** 结论：仍有洞（P1，"本机范围内自然成立"不成立）。
- `_desired_symbols` 实查 `live_task WHERE status='running'`，无 account/market 过滤（md_hub/main.py:269），D26 迁移项 11 还明写"保持全局"（D26:157）；§十一 只过滤 SA4/worker，未碰订阅。
- 场景：astock 分片机 hub 仍订阅 crypto/他市场 running 标的 → XTP 白订阅报错；两机同市场 L2 授权费仍 ×N。
- 修：running 源按本 hub 的 `account_id` 过滤（持仓源已如此），临时订阅按 market 归属过滤；同步改 D26 项 11 口径，加"订阅集 ⊆ 本机账户标的"守门。

**4. N4 配置层** 结论：方向可接受（A06:166 已有"管道外不可指纹输入"先例），但洞需补（P1）。
- `/etc/quant/deploy.toml` 在 release 树外 → 回滚不覆盖、快车道不察觉漂移，是新的机器隐式状态。
- 场景：host_vars 漏渲染/写错 → 一台仍跑旧市场集合；若文件缺失缺省"全市场"，两机同名 hub 各拿本机 Valkey 租约双开（叠加 N1 洞 4）。
- 修：①缺失/非法=EX_CONFIG 78，严禁 default all；②每 release 把渲染值与 md5 落入 release 树留证；③A06 §11 输入→消费者表加行+"改它须 force_full"；④Ansible 断言各机 `deploy_markets` 两两不相交，启动日志打印解析结果。

**5. F3 平仓兜底链** 结论：仍有洞（P1，第 3 级在最需要时必废）。
- 第 3 级只在第 2 级失败（跌停无买盘→opponent=0）时触发，此时 latest=跌停价，×(1−10%) 必低于跌停 → 交易所全拒，10 轮耗尽转人工=持仓裸露（20% 板同理：−20%×0.9=−28%）。
- 第 4 级"不挂单转人工"与本节自订原则（禁止无规则→不动作）矛盾；无人值守=放弃；若 `order_types` 含市价/对手方最优应先发市价。
- 修：第 3 级=最新价本身（SELL 钳 lower_limit / BUY 钳 upper_limit，band 未知时不外推）；第 4 级先试市价档再告警+人工；S9 断言"价在 band 内且被受理"，非仅"有单发出"。

**6. F4 close_policy** 结论：仍有洞（P1，两个新双源）。
- `has_limit_band`：一边"留 provider 维/迁 accounts.params"，一边"引用 band_rules"——键还在就是 provider/账号级布尔，账号同时持股票+可转债时一个值管两类，与逐 symbol 的 band_rules 事实冲突。
- `price_tick`：0175 里也是 provider 级（缺省 0.001，0175:40），但 §7.2 已把 tick_size 归 security_master（缺省 0.01，0092:47）→ 同一平仓价两步长，股票按 0.001 取整出非法价（adapters.py:214）。
- 修：两键都**删**（不是迁）——has_limit_band=band_rules 当前 phase 有无行，price_tick=InstrumentSpec.tick_size；只留 opponent_depth/aggression/max_order_qty 在账号/provider 维；守门钉"close_policy.*.{has_limit_band,price_tick} 已不存在"。

- 结论：**仍须改（剩余 P0：N1、N4 缺失缺省）**，其余 5 条为 P1/P2，可在 P1a 后按批修（F3/F4 必须赶在 P2 规格批前落，否则兜底链与双源随代码固化）。
- P0 按严重度：① N1 租约原子性/fencing/DB 故障态未定（跨机双会话→双下单）；② N4 文件缺失/非法无 fail-fast（默认全市场→hub 双开，放大①的本机租约盲区）；修法同一处：无有效租约不建连 + 配置缺失即 78。
