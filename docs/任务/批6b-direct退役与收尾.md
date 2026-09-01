# 批 6b:direct 退役 + hub 模式 EVENT_LOG 修 + shadow 收尾(2026-09-01 立项,验证日绿后解锁)

> 12 号 §2.10 原文:"收口:ST7 门禁→阶段 1 切换→direct 退役+全量联合验证+模块契约回写"。
> 08-31 验证日全绿(241 根零丢失+窗开关闭环)——批 6b 解锁。

## 产出(限定范围)

### 1. hub 模式 EVENT_LOG 修(批 4 遗留)
- **文件**:`server/src/strategy_runner/main.py` `_run_hub_mode()` 段(~L169)
- **问题**:hub 模式创建 `EventEngine()`+`ThinTdGateway` 但未注册 `EVENT_LOG`——TD 会话日志(连接/断开/重登)被静默吞掉(md_hub 批 0 修过同款盲区,批 4 迁移漏带)
- **修法**:在 `ee.start()` 后加 `ee.register(EVENT_LOG, on_log)` + 定义 `on_log` 转发到 logger(与 md_hub/main.py:159-166 同款)
- **验收**:worker 启动后 journalctl 可见 TD `[gw]` 连接/登录日志

### 2. direct 主循环退役
- **文件**:`server/src/strategy_runner/main.py` L393-702(~310 行)
- **内容**:删除 direct 模式代码路径(MainEngine+XtpGateway 全栈);`_md_mode()` 改为仅读任务级 params(全局 md_mode 已=hub,不再回退 direct);入口直接调 `_run_hub_mode()`
- **不退役**:入口分派骨架/_run_hub_mode/_warmup_history/SA4 退出码/依赖探活(worker 共用)
- **回滚**:批 6b 发布后回滚=rollback.yml(direct 代码在旧 release 可恢复)
- **清理**:direct 专属 import(MainEngine/XtpGateway 等)同步删;测试文件中 direct 相关用例跳过/删

### 3. shadow 停写收尾
- **现状**:bar_shadow 已无写入(worker 转 hub 后 on_vnpy_bar 不再执行);表保留历史供回溯
- **动作**:①三查手册②标记"已退役"(已有);②scheduler 对账任务的 shadow 双写代码清除;③hub 模式无 shadow 侧——无需新代码
- **不删表**:历史数据保留

### 4. 三查②继任(hub 单侧健康检查)
- **替换**:bar_hub vs bar_shadow diff → hub 单侧完整性(行数+缺口检测)
- **指标**:①行数(9:31~15:00 应=241 根/符号)②hub 心跳(bars/gen/subs)③worker 心跳(bars/lag/frozen)
- **落点**:flow/待办.md 三查手册②节改写(命令内联)

### 5. 模块契约回写
- `docs/architecture/模块契约/strategy_runner.md` 更新:direct 段删除+hub 模式段补 EVENT_LOG
- `docs/architecture/模块契约/strategy_framework.md` 更新:runtime 骨架引用面

## 风险
- direct 退役后无回退路径(除 rollback)——但 hub 模式已验证 2 个交易日(08-31 全绿+09-01 例行)
- EVENT_LOG 注册可能增加日志量(原被吞的现在出来了)——预期可控(TD 连接事件低频)
- 测试面:direct 相关测试(~15 个)需跳过或重写——跳过优先(重写属后续)

## 验收
- `pytest tests/ -q` 全绿(或 direct 相关用例标注 skip)
- staging 彩排绿→prod 发布→9:05 窗开+9:31 首根+TD 登录日志(journalctl 见 [gw])
- 三查②新手册可执行

## 参考
- docs/architecture/12-实盘稳定性设计.md §2.10(批次表)
- docs/architecture/14-设计.md v2(ST7)
- docs/任务/批4-worker迁移与trading解耦.md(worker 骨架)
