# 批50:LLM 模型页重构(两 section+拖拽 N 行链+流量曲线卡片+冷却三配置+预算链彻底退役)

> 2026-09-18 用户裁定(反馈池 #14)+澄清。参考批43/47 通道拖拽+系统监控卡片。
> **方案盲审 v2**:A(P1×4/P2×5)+B(P1×2/P2×5)全吸收+用户三裁定(冷却语义澄清/priority DROP/预算告警彻底停)。

## 一、现状(勘察实锤)

- `LLMModels.vue` 两卡:①「模型配置」(模型列表+编辑弹窗,内嵌子卡=「用量监控」当日表+7 日文本)②「预算预警」(表+弹窗+手动检查)。
- 模型表 `llm_model_config`:priority(1-100,default 10——存量大概率并列 10);网关 `ORDER BY priority`。
- **网关 failover 硬编码二级**:`_get_primary_fallback` 只取 `[0]/[1]`,`_do_chat:288`/`_do_chat_stream:325` 迭代 `[primary, fallback]`——**第 3 行起永不参与**(盲审 A-P1-1 实锤)。
- 熔断:`_is_circuit_open`(threshold 5/pause 300s/半开试探)——**键=provider**(同 provider 多行共享计数,与拖拽行级语义错位);参数来自 config.yaml 静态。
- 预算链:beat 每小时(app.py:183)→check_budget_alerts→铃铛 warn;CRUD 三端点;`create_llm_model` 缺 `reload_models()`(既有 bug,A-P2-1)。
- 用量 `llm_usage`(ts/provider/model/tokens/latency/success);summary 端点消费=本页唯一。

## 二、方案(v2)

### A 用户裁定(全部入案)

1. **冷却语义(澄清)**:模型 A 连续失败 N 次(短间隔)→切 B→**A 冷却 30min 期间完全不试**→期满恢复试 A(半开试探=恢复路径)。三配置项:`llm_cooldown_min`(默认 30)/`llm_fail_threshold`(默认 5)/`llm_retry_wait_s`(默认 2)——全入 system_config(bounds+desc),未配=新缺省**新真源**,config.yaml 降为读库失败兜底。
2. **priority DROP**(裁定 A):迁移 drop_column,downgrade 重建回填 position+1;发布走 `-e allow_contract=true` 通道(批11A 先例)。
3. **预算链彻底退役**(裁定 B):UI 卡+CRUD 三端点+beat 条目+tasks 任务+budget.py 检查链+`llm_budget` 表 DROP(0088 随版;downgrade 重建空表——批39 channel_config 先例)+test_llm_budget.py 退役+词条域退役。

### B 后端

1. **迁移 0088**(allow_contract 通道):
   - `llm_model_config` 加 `position`(存量物化 `ROW_NUMBER() OVER (ORDER BY priority, id)-1`——tiebreaker id,A-P2-2)+**DROP priority**;downgrade=加回 priority(`position+1`)。
   - **DROP `llm_budget` 表**。
   - system_config INSERT 三键(cooldown 30/threshold 5/retry_wait 2)。
   - schema_expectations:模型表行+position-priority;llm_budget 行删。
2. **网关 `gateway.py`**:
   - `_load_models_from_db` ORDER BY position;**failover 改 N 行链**——`_do_chat`/`_do_chat_stream` 迭代 `self._models` 全列表(行序即链序);`_get_primary_fallback` 退役。
   - **熔断键行级** `f"{provider}:{id}"`(A-P1-2——行=容灾单元,与拖拽语义一致)。
   - 三参数读 system_config:**实例缓存 TTL 60s**(锁外读,reload_models 顺带刷新——A-P1-3;锁内禁 IO);`_failover` config.yaml 降级兜底。
   - 网关内对 priority 的引用全清。
3. **端点(chat.py)**:
   - `/api/llm-models/reorder` POST(批43/47 同款,权限 `llm_config`,路由前移);模型 CRUD 端点+`LLMModelReq` 去 priority 字段;**create 补 `reload_models()`**(既有 bug 顺手修)。
   - `/api/llm-usage/series` GET 新:一次拉齐 `{models:[{provider,model,today:{calls,tokens,success_rate},series:[{ts,calls,tokens}]}]}`——**48h×小时粒度 48 点**(A-P1-4)+`generate_series` 左联补零(后端补齐前端零逻辑);**summary 端点随批退役**(B-P2-1,消费=本页唯一)。
   - 预算三端点(GET/POST/{bid}/**/check**)退役。
4. **scheduler**:app.py beat `budget-alert-check` 条目删+tasks.py 对应任务删。

### C 前端(LLMModels.vue 重写)

1. **两 section 平铺**:「模型列表」(上)+「用量监控」(下,独立卡)。
2. 模型列表:拖拽手柄列(批46 形态)+reorder 乐观重排;编辑弹窗去 priority 输入;`modelColDefs`/`emptyForm` 等 priority 三处清(A-P2-3);**create 后 reload** 一致性。
3. 用量监控:**卡片网格** `repeat(auto-fill, minmax(260px,1fr))`(B-P2-4 自适应)——每模型一卡(名/provider/当日调用/tokens/成功率+48h 流量曲线);**仿 SystemMetricsCards 新组件**(VChart 按需+cssVar 解实色——批22 约定;无阈值线);空态=无数据。
4. 排序说明行含冷却提示("失败连续 N 次冷却 X 分钟,运行配置可调")。
5. api.js:reorder/series 新增;budget 三导出+getLLMUsage 退役;`llm.priority` 词条退役。

### D 词条(文案师)

- 新:拖拽列/排序说明(含冷却提示)/卡片空态/曲线轴;desc 三键(cooldown/threshold/retry_wait)。
- 退役:llm.priority+llm.budget* 域。

## 三、测试钉

- reorder(全集/幽灵拒);series 形状(48 点补零/双键);**熔断测试 5 处改自适应读值**(B-P1-2:`gateway._pause_s()-1` 形态);**N 行链钉**(三模型,前两失败第三被尝试——钉死 A-P1-1 修复);行级熔断键钉(同 provider 两行独立);create reload 钉;迁移双向(priority 往返/llm_budget downgrade 重建)。
- 前端:build+守门+dev 手测(拖拽+曲线卡)。

## 四、流程

方案双盲审 ✅→编码→代码盲审→钉+build→staging→**prod(allow_contract 通道)**。15:05 后批47/48/49 先行上产,批50 独立窗口。

## 六、实施记录(2026-09-18)

- **方案盲审**:A(P1×4:N 行链硬编码实锤/熔断键 provider 错位/读配置锁内禁 IO/series 三缺)+B(P1×2:priority DROP 通道/熔断测试硬编码)全吸收 v2;用户三裁定(冷却语义澄清三配置项/priority DROP/预算告警彻底停)。
- **代码盲审**:A(P1×2:**supports_tools 硬编码 true 重置回归[本批引入]**/areaStyle cssVar 双参静默失效)+P2×3(N 行链钉补落);B 零 P1+P2×2(死键 13+文档三行)。全修。
- **dev 实测抓出存量缺陷**:坏密文炸单例构造(整个 chat 链断)——`_load_models_from_db` decrypt 在 try 外;根修单行跳过+warning。
- **验证**:pytest **1167 绿**(+N 行链钉[1,2,3]/熔断自适应×2;退役 test_llm_budget+summary 重写 series+36a 减断言)/build 绿/守门 1612(死键 13 退役)/dev 手测(reorder 幽灵拒+生效/预算 404/series 空态)/迁移 0088 双向重放(position 物化↔priority 回填+llm_budget drop↔重建)/schema_expectations/文档回写×2(llm_gateway+web_api 三行)。
- **部署面**:0088 破坏性 DDL 被 prod 门实证拦截(设计内)→ 走 `-e allow_contract=true`(用户裁定①A);批47/48/49 同车一次上。
- 踩坑:词条 regex 全局删键误伤别域(守门三层当场抓出吞键——回退改域内精确删);`#` 非 JS 注释语法直炸(批42 教训同源,复跑即抓)。
