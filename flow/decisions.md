# 决策日志 (decisions)

> 只记**仍然有效的架构决策**（为什么这样做）。已推翻的已清理；过程考古在 git log。
> 新决策追加在最上面。

---

## 2026-08-15 · N 语言架构约束：注册表驱动 + en 缺省

- **决定**：多语言设计为支持任意语言（当前实现 zh/en），英语为不匹配时缺省。所有语言相关逻辑改为**注册表驱动**（dict/array），不写死双语。
- **加新语言=只加条目零逻辑改动**：locales/index.js + i18n.js LANGUAGES + terms.py TERMS/LANG_NAMES + 邮件模板 dict。
- **各层策略**：页面=浏览器语言自动；条款=全语言纵向堆叠（不依赖检测）；邮件=操作者界面语言（请求传 lang）；LLM=跟随输入。
- **禁令**：写死 zh/en 二元判断、独立语言变量（TERMS_ZH）、硬编码语言列表。测试锁约束（test_new_language_only_needs_entry）。

## 2026-08-15 · 后端错误码化：字符串码 + 前端本地化映射

- **决定**：`ApiError(status, CODE, 中文兜省)` 响应 `{detail, code}`；前端 `apiErr(e)` 优先 `err.<CODE>` 本地化、无映射回落 detail。
- **否决数字码**（如 40001）：不自描述、要查表。
- 用户流程 22 处已迁移；深层管理接口增量补码（未映射自动回落安全）。

## 2026-08-15 · 末位 admin 保护：管理页移除 / 自助注销保留

- **管理页**：不可达（user_mgmt=admin-only + 不能动自己 ⇒ 操作者若是另一 admin 则目标非末位），删除死规则。
- **自助注销**：真实可达（用户对自己操作，不经管理页），`guard_self_deactivate` 单独设防（唯一启用 admin 不可注销自己）。
- **教训**：规则设计时验证可达性，不可达的规则是死代码。

## 2026-08-15 · 参考方案借鉴四批次（用户管理）

- **背景**：用户提供的邀请制用户管理参考方案，对比后 3 处原则冲突不借鉴（邀请预设 Trader / 超管分层 / 数字错误码），6 项借鉴分四批落地。
- **A**：禁用提示 / 邮箱登录 / last_login / JWT jti 黑名单
- **B**：邀请记录列表+撤销
- **C**：昵称+头像+Profile 页+顶栏改造
- **D**：软删除+脱敏+自助注销

## 2026-08-14 · SMTP 配置 DB 化（弃 .env）

- **决定**：SMTP 五项走 `system_config`（前端系统配置页可改），`.env` 不再参与。`SMTP_DEV=true` 仅本地显式开发模式。
- **理由**：单一真相源；.env 难改且易残留旧值（BASE_URL IP 问题的教训）。

## 2026-08-14 · 通知中心：类别×角色可见 + 仅实盘紧急外推

- **三项用户决策**：① 可见范围按类别×角色（email→admin 等）；② 外部通道只推 risk+critical（实盘紧急），订阅型 report 保留；③ 告警历史从 Valkey 直接切 PG（旧数据不迁移）。
- **行为变化**：磁盘/接口健康 critical 不再外推（仅站内）。

## 2026-08-13 · DDL 全部入迁移 + 运行时 DDL 清零

- alembic 迁移为唯一真相源；`verify_schema()` 启动校验；30 处运行时 CREATE TABLE 全删。

## 2026-08-10 · Codex 失效，Claude 直接做

- subcodex 复杂任务返 Ark 400（function calling bug），当前 Claude 直接编码，full-subagent 铁律豁免。

## 2026-08-09 · 自包含任务文档体系

- 新待办按 `docs/任务/<id>.md` 写（8 字段），做任务只读任务文件+接口契约+模块契约，零代码阅读。

## 2026-08-08 · 平台化通用架构方向

- 6 大接口抽象（DataSource/Broker/MessageChannel/Task/RiskRule/LLMProvider），别人配置+实现接口子类即可接入，不改平台代码。

## 2026-08-07 · LLM 网关简化

- 移除 tier 机制（按 priority 全局排序）+ 移除语言注入（LLM 按输入语言自然回复）。

## 2026-08-03 · 策略+回测体系架构

- 四层（Strategy/Factor/SignalAggregator/Adapter）+ ActionSignal 契约 + 因子注册制 + 风控覆写 + DSL/Python 双模式统一执行。

## 2026-08-03 · 策略实盘化：修正版 B

- 每策略独立子进程（systemd quant-strategy@<id>）+ 独立 vnpy MainEngine + XtpGateway 实时驱动（tick→BarGenerator→on_bar）。

## 2026-08-03 · A 股进实盘开关（废止只读）

- AStockReadonlyAdapter 废止，A 股统一走 XTPAdapter；实盘三级开关 AND：.env 总闸 + Web 分项 + 策略级。

## 2026-08-17 · 实盘链路验证收尾（新架构定语义 + 运维硬化）

- **live_task 是新架构唯一运行语义**：runner 停止条件查 `live_task.status`（stop_live_task 置 stopped）；`strategy_config.enabled/backtest_verified` 只作创建/启动时的门禁。双 unit 归位：`quant-strategy@`（--id 旧架构）/ `quant-live-task@`（--task-id 新架构），polkit 两者都放行。
- **服务器加 2G swap**（1.8G 内存跑不动 runner 的 XTP 合约加载尖峰，全局 OOM 实锤）；runner MemoryMax=1G。
- **部署实例清单真相源 = DB**（feishu_config），不再"收集 active 实例"（会把误启的幽灵转正）；install-services 以 root 跑 + restart polkit + 输出 unit 状态快照。
- **当天验证结果**：tick→bar→on_bar→signal→risk→XTP order 全链通（证据在进展.md §0）；trade_log 写入缺失转 #46。
