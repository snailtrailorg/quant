# 批 164c · 横截面 DSL（期 3/3）——立项稿（与 164b 互印证后编码）

> 方案真源：`flow/方案/标的筛选多因子化方案.md` v3（§二.4 P1-3/P1-4 处置即本批契约）。
> **互印证纪律（用户裁定 2026-11-02）**：b/c 方案先做全互相印证，再分别编码——本文件 §印证即交叉面。

## 实施步骤
1. **横截面校验通道**（strategy_framework/factor.py 扩——独立于时序链）：`validate_cross_section_expr(expr, category)`
   - 变量词表=注册表该品类全部字段名（六元组 name——含 164b 补进的四元老/双低溢价）
   - 函数词表=`rank(x)`/`zscore(x)`/`percentile(x)` + 数值函数（abs/max/min/round——_DT_FUNCS 子集）
   - **禁时序窗口函数**（mean/close 等时序词表零交集——P1-3 词表分离立法）
   - 复用 `_safe_eval` 求值壳（AST 白名单/500 字符限）——校验前置新通道
2. **CrossSectionContext**（screen.py 扩）：`run_cross_section(category, filter_expr, rank_expr?, limit)`
   - 数据面=BASE_FROM 品类当日全集（**与 164b 编译器同一 BASE_FROM 单源**——印证点 ①）+预计算因子列（cache JOIN 同源）
   - 求值=逐标的一行 dict（字段名→值）→ filter_expr 求值 bool 剔除 → rank_expr 求值排序取 TopN
   - NULL 传染（P1-4）：行内任一引用字段 NULL → 该标的 filter 判 False 剔除（与 164b NULL 立法同向——印证点 ②）
   - 横截面函数语义：rank(x)=当日全集内的归一排名 [0,1]；zscore=标准化；percentile=百分位——**母数=pre-filter 全集**（与 164b percentile 母数同口径——印证点 ③）
   - 资源面：单表达式横截面调用数 ≤10（P1-4）；limit 帽 500（与 164b 同——印证点 ④）
3. **端点**：POST `/api/screen/run-dsl`（读键——与 164b `/run` 同权限面同限流；body={category, filter, rank?, limit}）
4. **preset 集成**：definition dsl 式={v:1, filter, rank?}——source='dsl'（与 164b 同表同 CRUD 同权限——印证点 ⑤；写侧校验=validate_cross_section_expr 干跑，对齐 164b 写侧干跑先例）
5. **前端**：三屏「高级」页签填充（164b 留的占位）——Filter 表达式输入+可选 Rank 输入+试跑按钮+错误提示（校验错码化：XSEC_UNKNOWN_FIELD/XSEC_FORBIDDEN_FUNC/XSEC_TOO_MANY_RANK/XSEC_SYNTAX）；方案另存 source=dsl 进同一列表（加载按 source 路由到对应页签——**不互转**P1-5）

## 印证面（与 164b 五点——编码前双方确认单源）
| # | 契约 | 单源在哪 |
|---|---|---|
| ① | BASE_FROM 品类模板 | data_platform/screen.py 常量（b 编译器与 c 求值器同引） |
| ② | NULL 剔除语义 | 同向：比较级 NULL=不入选 |
| ③ | 横截面母数 | pre-filter 品类当日全集 |
| ④ | limit 帽 500 / 权限面 | 同端点族同规 |
| ⑤ | preset 表/CRUD/权限/写侧干跑 | 164b 建物，c 只加 dsl source 形状 |

## 限定范围
factor.py 扩校验通道（不动时序链一行）+screen.py 扩求值+routes/screen.py 加端点+前端高级页签。不碰：注册表新增字段（b 域）/旧 GET 端点/盘后 task。

## 验收（可回归）
- 校验通道：时序函数混入=XSEC_FORBIDDEN_FUNC；未知字段=XSEC_UNKNOWN_FIELD；rank 嵌套 rank=XSEC_TOO_MANY（若 10 上限语义定嵌套计）；语法错=XSEC_SYNTAX
- 沙箱负测试：表达式试 `__import__`/`open(` → XSEC_SYNTAX（AST 白名单拦截）
- 语义断言：小数据集（10 标的）rank/zscore 手算对拍；NULL 行剔除；与 164b builder 同条件结果等价性抽测（印证点 ② 的回归化）
- preset dsl 形状 CRUD+加载路由正确页签

## mock 库
求值单测=纯内存 CrossSectionContext（list[dict] 直灌）；端点测试走 dev 真库。

## 参考（≤2）
- 方案 v3 §二.4
- 164b 任务文件（印证面对照）
