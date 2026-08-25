# 批1：SDK 状态机 + 冒烟基座（运行时重构第 1 批）

> 来源：运行时重构计划（2026-08-25 批准，12 号 §2.9）。本文件自包含，配合 `docs/architecture/接口契约.md` 与 `docs/architecture/模块契约/strategy_framework.md`（如无则以本文件契约为准）。

## 目标
终结 2026-08-25 SEGV 事故类：XTP SDK 调用收进生命周期状态机（非法时刻调用抛 Python 异常、永不到 C 层），`setHeartBeatInterval` 以官方时序（createQuoteApi 之后、login 之前）回归；建立 7×24 可跑的本地真机冒烟门（mock 拦不住 C 层的缺口补上）。

## 依赖（就绪）
- 批 0 ✅（止血 c8326bd + 归档 + 生产恢复四证确认）
- `md_session.py` 2026-08-25 修订已在产（renew logout 清场语义——本批移交守卫）
- XTP 测试平台 7×24 可达（用户确认）；本地 `vendor/xtp/lib` + venv vnpy_xtp 就绪

## 产出
| 文件 | 动作 | 内容 |
|---|---|---|
| `server/src/strategy_framework/md_api_guard.py` | 新建 ~200 行 | `SdkState`/`SdkLifecycleError`/`GuardedXtpMdApi` |
| `server/tests/test_md_api_guard.py` | 新建 | FSM 全转移矩阵（打桩 C 方法，不碰真 SDK） |
| `server/src/strategy_framework/md_session.py` | 修改 | `renew()` 改调 `md.relogin()`；`_logout_quietly` 删除（移交守卫） |
| `server/tests/test_md_session.py` | 修改 | TestRenewTeardown 重写为 relogin 契约 |
| `server/src/md_hub/main.py` | 修改 2 行 | 构造点 `XtpMdApi(gw)`→`GuardedXtpMdApi(gw)` + import |
| `server/src/strategy_runner/main.py` | 修改 3 行 | `gateway.md_api = GuardedXtpMdApi(gateway)`（connect 前）+ import |
| `scripts/smoke/run_md_lifecycle.py` | 新建 ~200 行 | 真机冒烟：登录→收 tick→relogin 往返→再收 tick→干净退出 |

## 限定范围
不碰：hub/runner 主循环其他段（批 2/3）、vnpy_xtp 包本体、心跳 schema、告警链、bar 口径、部署脚本。引擎只动构造点行。

## 接口契约

**新** `GuardedXtpMdApi(XtpMdApi)`（`md_api_guard.py`）：
- `connect(userid, password, client_id, server_ip, server_port, quote_protocol, log_level) -> None`
  IDLE/DEAD 态合法；内部序 `createQuoteApi → setHeartBeatInterval(15) → login`；成功→LOGGED_IN，登录失败→CREATED，重复 connect→`SdkLifecycleError`
- `relogin() -> bool`：CREATED/LOGGED_IN 合法（LOGGED_IN 先 `logout` 清场——官方 -2 序列；失败再补一发清槽）；IDLE/DEAD 抛 `SdkLifecycleError`。返回=login 同步结果
- `login_server() -> bool`：覆写，**永不抛**（父类 `onDisconnected` 在 SDK 线程回调它）；IDLE/DEAD 态 no-op 返回 False
- `subscribe(req)`：非 LOGGED_IN 态 no-op+debug（软防护，重放场景需要）
- `onDisconnected(reason)`：状态→CREATED + 观测日志，再走父类原行为（单次重登）
- 状态卫生：每次 `_login` 前强制 `connect_status=login_status=False`（防陈旧 True 假成功——vnpy 失败路径不清标志的坑）

**改** `XtpMdSession.renew() -> bool`（`md_session.py`）：调 `md.relogin()`（bool=已确认/未确认，退避照旧翻倍）；`SdkLifecycleError` 捕获→warning+False。

## 双盲审核（2026-08-25 收卷，A/B 独立同判）

**总判定双同：可保留在产、无 P0**。B 附加 `.so` 实证：vnxtpmd 无 `gil_scoped_release` → C 方法全程持 GIL → 线程交错无 SEGV 级后果。热修已随卷落地：**P1-2 续航未确认回滚重试**（schedule_due 预落当日标记 + renew 丢弃 ok 值 → 09:10 未确认则开盘盲至 ~09:40；修=未确认回滚 `_renewed_date` + 窗口内按退避重试，489 绿）。

剩余发现按批次归口（详见 flow/待办.md 批 2/5 清单）：P1 守卫加锁+intentional-logout（批 2 mdlink）、P1 阻塞 login vs WatchdogSec 90s（批 2）、P1 restart_server 稳定检查杀链（批 5）、P1 指纹空兜底死代码（批 5）、P2×8（client_id 校验/参数双机制/_unsubscribe 态门/DEAD 落位/login_server 永不抛结构化/凭证 env 化/补用例/多任务共号文档化）。

## 验收结果（2026-08-25 执行记录）
1. ✅ FSM 矩阵 14 例 + session 契约 6 例，全量 487 绿，分层 4 绿，pyflakes 零新增
2. ✅ G2 真机冒烟（服务器，`--client-id 2`）：登录→tick 3→relogin 往返→tick 3→干净退出，exit=0 零 SEGV
3. ✅ G4 部署：hub 一次稳定；「已确认」relogin 生产首秀；任务 8 client 2 登录成功
4. ⏳ 明早 09:10 窗口内定时续航观察（收官项）

## 批内新发现与追加修复（均已完成）
- **XTP 会话规则实锤**（官方 CreateQuoteApi 注释）：同账号同 client_id 仅一个 MD 会话——
  hub(1)/runner(1) 撞号即 08-22 起任务 8 "user already exists" 全部真相。修复：runner
  独立号 `broker_config.params.client_id_runner=2`（bf9d7a5），双轨恢复（14:15 起落数）
- **定时续航窗口化**（7d9fcaf）：原 schedule_due 是"09:10 后未续过"——盘中启动的进程会
  立刻无谓 renew 健康会话（14:05 自杀式 churn + 槽回收竞态自盲 10min 实锤）。改窗口
  09:10-09:30，盘中启动零续航
- 冒烟进程退出未 logout → 僵尸会话被下一登录清算（10200006 断线）——后续批 2 冒烟套件
  增加收尾 logout

## 验收标准（原定）
1. `cd server && venv/bin/python -m pytest tests/test_md_api_guard.py tests/test_md_session.py -q` 全绿（含 FSM 矩阵：**connect 序内 heartbeat 位置断言**——今日 SEGV 的回归锁）
2. `venv/bin/python -m pytest tests/ -q` 全量绿；`venv/bin/python -m pytest tests/test_layering.py -q` 绿
3. `cd server && LD_LIBRARY_PATH=vendor/xtp/lib QT_QPA_PLATFORM=offscreen venv/bin/python scripts/run_md_lifecycle.py` 退出码 0，输出含「登录成功」「tick>0」「relogin 往返 OK」「干净退出」（真机；dev 机到测试平台网络不通 OS:115 实测——**冒烟在服务器跑**，部署前用 /tmp 暂存 + PYTHONPATH 前置零生产触碰）
4. G4（盘外）：随下次部署上线，观察 hub 正常收 tick + 09:10 续航日志

## mock 方式
- FSM 测试：真构造 `GuardedXtpMdApi(fake_gateway)`（fake 为记录型 stub），实例级打桩 `createQuoteApi/setHeartBeatInterval/login/logout/getApiLastError/query_contract/init`——不连服务器、不建真 C 会话（构造安全，今日 SEGV 是"构造前调方法"）
- session 测试：`md = MagicMock(); md.relogin.return_value = True/False`；异常路径 `side_effect = SdkLifecycleError`
- 冒烟：不 mock，真连测试平台（SETTING 沿用 `scripts/test_xtp_connect.py` 测试账户约定）

## 参考文档
1. `docs/architecture/12-实盘稳定性设计.md` §2.8/§2.9（韧性模型与重构决策）
2. `docs/reference/xtp-sdks/XTPXQuoteAPI…/header/xtpx_quote_api.h`（官方 Login -2/心跳时序语义）
