# 批189 E1/E2/E5/E9 盘中实测 Runbook（prod）

> 前置：admin token（`/home/bernard/.config/quant/prod_admin.env`）、API 基址 `https://quant.snailtrail.cc`；
> 所有命令只读优先，涉及下单/停服的步骤须先确认测试账号与持仓。

## 公共取证

```bash
set -a; . /home/bernard/.config/quant/prod_admin.env; set +a
export QUANT_API_BASE=https://quant.snailtrail.cc
API="deploy/scripts/api_curl.sh"
bash $API '/api/interfaces/status?kind=md'        # 运行/连接/config_ok
bash $API '/api/health/components'                # hubs: subs/ticks/bars/connected/sub_fail
bash $API '/api/alerts/active'                    # 未消除告警
```

## E2 订阅隔离（只读，先做）

1. `GET /api/health/components` 记录 `md-binance-perp`、`md-okx-perp` 的 `subs/ticks/bars/sub_fail`。
2. 隔 5 分钟再看一次：币安 `bars` 应递增、`sub_fail=0`；OKX 不应出现 `*_SWAP_OKX`（日志），A 股代码不应出现在加密 hub 的 subs。
3. 日志（需 journal 权限）：`journalctl -u quant-md-hub@md-okx-perp --since '10 min ago' | grep -c _SWAP_OKX` 应为 0。

**当前偏差**：心跳只有 `subs/ticks/bars/sub_fail`，**没有 `reset_cnt`**（M4 验收要求三计数含 reset）；且本轮 prod 采样 `ticks=0/bars=0`，E2 目前不能判绿，需先查加密 hub 订阅/行情。

## E9 告警 code 化（低风险，可盘中）

1. 选**非交易主用**接口（建议 `md-okx-perp`，无持仓；先确认无 live task 依赖）。
2. michael 执行：`sudo systemctl stop quant-md-hub@md-okx-perp`
3. 60–120s 内：`GET /api/alerts/active` 应出现 `hub_hb_lost`（component=`md-hub:md-okx-perp`，critical）；日志无 ValueError 堆栈。
4. 立即 `sudo systemctl start quant-md-hub@md-okx-perp`；再查 `/api/alerts/active` 该条消失、历史卡出现恢复记录。

## E5 三路下单 + 急停

- **反例（先做，不下真单）**：`POST /api/manual-order` body `{"account_id":"td-emt-emq",...}`（已停用）→ 期望 `IFACE_IDLE`/拒单，且 `order_log` 无新行。
- **手工单**：`POST /api/manual-order`，`account_id="td-xtp"`（新校验已改为 code）、`confirm=CONFIRM`、100 股、限价；查 `GET /api/manual-order/{req_id}`、`risk_log`、`order_log.account_code=td-xtp`。
- **定时 cancel-all**：`POST /api/risk/cancel-all` body `{"accounts":["td-xtp"]}`；查回执与 `td:cmd:td-xtp` 消费日志。
- **策略单**：启动/复用 live_task（td-xtp），等策略信号；查 `order_log` + `td:cmd:td-xtp` 消费。
- 证据：三条 `order_log/risk_log`、各 `risk:cmd:result:<req_id>`；反例响应码 = IFACE_IDLE。

**注意**：manual-order 原先强制 int account_id，已修为 `td-*` code（未发布前 prod 仍会 400）。正例会产生真实委托/成交，须用户确认测试账号与标的、价格。

## E1 清仓取价主备

1. 前提：td-xtp 有持仓（先 `GET /api/positions` 确认）+ `td_md_map` 配主备（md-xtp p1、md-emt-emq p2）。
2. `POST /api/risk/close-all {"confirm":"CONFIRM","accounts":["td-xtp"]}`；日志应含 `quote_key=hub:latest_tick:md-xtp:<vt>`。
3. 停主 md（`systemctl stop quant-md-hub@md-xtp`）后重复清仓（需仍有持仓/或单标的测试）；日志应降级到 `md-emt-emq`。
4. 反例：清空映射→`MAPPING_MISSING`；停全部行情→`NO_QUOTE`；两码不得混用。
5. **风险**：真实卖出持仓；须在测试账号/授权标的上做，建议开盘 30min 内、仓位最小化。

## 判定

- E1/E5 依赖真实委托/持仓，**需用户明确授权**后才能执行；
- E2/E9 可先做只读/停非主用 hub；
- 当前 E2 有 `reset_cnt` 缺失与 ticks/bars=0 的偏差，先排查再判绿。
