# W6 编辑器与字体收官:Monaco DSL 补全+中文 web 字体分包+两缓做接线(2026-09-01;完美系统战役第六批)

> 承 W5 缓做(readonly 接线/Reconcile v2)+战役表 #2a/#3。

## 产出 1:DslEditor(Monaco DSL 补全,#2a)

- **新建** `web/src/components/DslEditor.vue`:仿 PythonEditor 封装——注册自定义语言 `quant-dsl`(tokenizer:字段/函数/数字/运算符)+ **completion provider**(trigger '.'与任意输入):
  - 字段:close/high/low/open/volume(+open_ 别名)——文档"当前 bar 标量"
  - 函数:mean/std/max/min/ema/rsi/slope/avevol——签名 `mean(field, n)`+文档"窗口 n 根(含当前)"
  - snippet:`mean(close,20) / close - 1` 模板
- **接** Factors.vue DSL 表达式框(el-input→DslEditor,高度 120px;placeholder 同)
- monaco-editor 0.56 已有,零新依赖

## 产出 2:中文 web 字体分包(#3)

- **源字体决策**:MiSans 本机无源(需小米官网下载,直链不确定+体积大)——**首选探测 MiSans 直链(spe curl,单字重 Regular ~10MB);不成则 Noto Sans SC(系统 google-noto-sans-cjk 包,开源同权)替代**——目标=跨端统一中文渲染,非特定品牌(源差异记 decisions)
- **工具**:`npm i -D cn-font-split`(7.4.3,registry 已通);脚本 `web/scripts/split-font.mjs`:输入源 ttf/otf → 输出 `public/fonts/cjk/` 子集(woff2+unicode-range CSS 片段)
- **接线**:tokens.css 补 `@font-face MiSans Web`(或 `NotoSansSC Web`)系列——`font-display: swap`,子集 CSS import;`--font-ui` 栈把 Web 版插在系统 MiSans 前
- **验收**:build 后 dist/fonts/cjk/ 子集齐;首屏中文由 Web 字体渲染(非系统回退);总增量控制在常用 3500 字子集 ≤300KB(盲审视点)

## 产出 3:readonly 接线(W5 缓做)

- 6 重点页 inject `navReadonly` → 主写按钮 :disabled+tooltip("只读(菜单权限)"):Strategy(新建/保存)/LiveTask(启动/停止)/Pool(回补)/BacktestRun(发起)/RiskRules(增删改)/DataOps(同步触发)
- 执行面声明(已定):api 维服务端拒,UI 灰=提示层

## 产出 4:Reconcile v2(W5 缓做,交互重构)

- expand 列 → **行点击详情抽屉**(el-drawer 右侧,内容=原 expand 面板四行)——v2 兼容形态
- 表体 el-table→el-table-v2(≤500 行;列:4 数据列+操作列 cellRenderer 处置按钮);脱敏分支(count/aggregated 摘要,同 Risk 页范式)

## 验收
1. build 绿+冒烟绿;DslEditor 输入 `mea` 弹补全(手验路径描述);DSL 保存/试算不回归
2. dist 含 cjk 子集;禁系统字体(devtools)后中文仍正确渲染
3. 6 页 readonly 灰(nav 配 readonly 态本地验);Reconcile 抽屉+虚拟滚动+脱敏分支
4. pytest 全绿(后端零改动预期;若字体脚本入库则 node 侧无测试,验收靠 build 产物)

## 风险
- MiSans 直链失败率高 → Noto 替代路径保底(决策记录)
- cn-font-split 产物体积失控(全字符集)→ 脚本限定 subset(常用简中 3500+ASCII);CI/构建不阻塞(产物预生成入库,非每次 build)
- v2 Reconcile 处置按钮 cellRenderer(事件闭包)复杂度——操作列保 el-button 组件渲染
