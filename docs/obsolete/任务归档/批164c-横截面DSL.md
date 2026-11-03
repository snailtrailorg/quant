# 批 164c · 横截面 DSL（期 3/3）——双盲处置 v2

> 方案真源 v3 + 164b 任务（印证面）。A（2P0+3P1）B（1P0+4P1）全收编，对照文末。

## 实施步骤
1. **层位归位**（A-P0-1）：校验+求值整体放 **strategy_framework/cross_section.py 新文件**（import data_platform.screen 词表/BASE_FROM 构造器=层2→1 合法先例）；screen.py 只供 BASE_FROM/字段集；routes 层4 组合。禁复制式（求值壳不复制——漂移面立法）
2. **求值壳扩节点**（A-P0-2）：_safe_eval 参数化节点集注入——横截面专用放开 Compare/BoolOp/UnaryOp(not)（时序链零改动——节点集默认现值）；正测试（布尔式可求值）+负测试（时序 DSL 传布尔式仍拒）
3. **两通道词表单源**（A-P1-3）：_XSEC_FUNCS（rank/zscore/percentile+abs/max/min/round）一处定义——校验通道与求值 env 同引；_DT_FUNCS 里横截面外的在 env 构造时排除
4. **双遍求值模型**（A-P1-2）：①AST 预扫横截面调用（禁嵌套 rank(rank(x))=XSEC_NESTED）→②参数子式逐行求值成向量→③向量化 rank/zscore→④回填→⑤逐行终求值（filter bool 剔除/rank 数值排序）
5. **NULL/母数/顺序**（B-P1② ③/A-P1-1）：取全集（build_base_from(category, fields) 单源含 SELECT 映射与 cache join 别名规则——日期错位语义进测试）→算横截面列（剔参量 NULL——母数=pre-filter ∩ 剔参 NULL 与 b 同口径）→filter 求值（引用字段 AST 全集任一 None 判 False 剔除）→排序 TopN。等价面收窄：match=all+单字段比较等价（any 模式/NULL-OR 场景两链有意不同——文档化差异）；无 rank 默认序 total_mv DESC
6. **端点**：POST /api/screen/run-dsl（body={category, filter, rank?, limit≤500}——与 b /run 同族规：同权限面同限流同错误码族）
7. **preset dsl 形状**（B-P1⑤）：b source 判别已改形状单源（definition.match=builder/filter=dsl）；c 只加 dsl 分支干跑校验（validate_cross_section_expr），b 逻辑零改
8. **前端**：高级页签填充（Filter/Rank 双输入+试跑+错误码 i18n：XSEC_UNKNOWN_FIELD/XSEC_FORBIDDEN_FUNC/XSEC_NESTED/XSEC_SYNTAX/XSEC_TOO_MANY）；另存 source=dsl；加载按 source 路由页签（不互转）；b 占位同文件拆除（b 先并 c 后并）

## 验收（v2 增补）
v1 全项+：布尔式正测试/时序链零影响断言/两通道词表一致性断言（_XSEC_FUNCS 同引比对）/filter 掉行不改 rank 值/BASE_FROM 日期错位测试/无 rank 默认序断言

## 双盲处置对照（A/B 同判：修后可开工）
| # | 发现 | 处置 |
|---|---|---|
| A-P0-1 层位违规 | cross_section.py 新文件归 strategy_framework | ↑1 |
| A-P0-2 求值壳无布尔节点 | 节点集参数化注入（时序链零改） | ↑2 |
| B-P0 SELECT 列集未定义 | build_base_from 含 SELECT 映射 | ↑5 |
| A-P1-1 NULL 等价假 | 等价面收窄+差异文档化 | ↑5 |
| A-P1-2 双遍/嵌套 | 显式五步+XSEC_NESTED | ↑4 |
| A-P1-3 词表两源 | _XSEC_FUNCS 单源同引 | ↑3 |
| B-P1① BASE_FROM 形态 | build_base_from 组合函数单源 | ↑5 |
| B-P1⑤ b 联动 | b source 形状单源（b v3 同步） | ↑7 |
| B-P2 /run 无落点 | b 任务 v3 补路由契约 | ↑6 |
| B-P3 默认序 | total_mv DESC | ↑5 |
