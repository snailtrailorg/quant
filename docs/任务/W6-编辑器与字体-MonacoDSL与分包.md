# W6 编辑器与字体收官:Monaco DSL 补全+中文 web 字体分包+两缓做接线(2026-09-01;完美系统战役第六批)

> 承 W5 缓做(readonly 接线/Reconcile v2)+战役表 #2a/#3。

## 产出 1:DslEditor(Monaco DSL 补全,#2a)

- **新建** `web/src/components/DslEditor.vue`:**模块级单次注册+IDisposable**(盲审 A-P1:补全 provider 是 monaco 全局,对话框反复挂载会叠重复——注册提升模块级做一次,组件只建 editor 实例+卸载 dispose);tokenizer 运算符对齐 factor.py `_DT_OPS` 全集(+ - * / ** // % 与一元±,盲审 A-P2);completion(任意输入触发):
  - 字段:close/high/low/open/volume(+open_ 别名)——文档"当前 bar 标量"
  - 函数:mean/std/max/min/ema/rsi/slope/avevol——签名 `mean(field, n)`+文档"窗口 n 根(含当前)"
  - snippet:`mean(close,20) / close - 1` 模板
- **接** Factors.vue DSL 表达式框(el-input→DslEditor,高度 120px;placeholder 同)
- monaco-editor 0.56 已有,零新依赖

## 产出 2:中文 web 字体分包(#3)

- **源字体定案(盲审 A/B 修)**:MiSans **非开源**(小米自定义条款,子集再分发边界模糊——"开源同权"系误称,直链探测已失败)→ **Noto Sans SC 静态字重** `/usr/share/fonts/google-noto-sans-sc-fonts/NotoSansSC-Regular.otf`(盲审 A:cn-font-split 不吃 VF ttc,静态 otf 本机在库)
- **OFL 合规三件(盲审 B-P0)**:①`public/fonts/OFL.txt` 随附(Noto 子集+**补 JBMono 历史欠账**);②子集=修改版,**禁用 Reserved Font Name**——CSS family 用别名 `NotoSansSC Web` 是合规机制非随意,注释注明勿"优化"掉;③wqy(GPL)不碰
- **工具**:`npm i -D cn-font-split`(7.4.3;注意 postinstall 拉 wasm32-wasip1 运行时);脚本 `web/scripts/split-font.mjs`:静态 otf → `public/fonts/cjk/` 子集(woff2+unicode-range CSS)
- **接线**:子集 CSS **改写 family** 为 `NotoSansSC Web`(盲审 A-P2:产物 CSS 用字体内部名须改写否则引用落空)→ tokens.css @font-face 系(font-display:swap);nginx http2 确认(盲审 B-P1:未证实——HTTP/1.1 下 100 分片×6 连接限制,FOUT 拉长;查 server nginx.conf 再定子集粒度)
- **验收口径修(盲审 A-P1)**:**首屏命中 chunk 下载量 ≤150KB**(tokens.css:19 既有预算)——非"3500 字总子集"(表外字回退系统=设计,与"禁系统字体仍正确"矛盾口径废除)

## 产出 3:readonly 接线(W5 缓做)

- 5 页 inject `navReadonly`(指路修,盲审 B-P0:原 6 页三处错):Strategy(新建/保存)/LiveTask(启动/停止)/**Backtest.vue:17/131 submitRun(非 BacktestRun.vue)**/**DataManage.vue:55-56 同步触发(DataOps 是 tab 壳零按钮)**/RiskRules(增删改);**Pool 缓做**(回补按钮模板无挂点——需先补模板按钮,另记);已有 :disabled 合并规则 `:disabled="原条件 || navReadonly"`(Backtest.vue:7/9)
- 执行面声明(已定):api 维服务端拒,UI 灰=提示层

## 产出 4:Reconcile v2(W5 缓做,交互重构)

- expand 列 → **行点击详情抽屉**(el-drawer;内容=原 expand **6 行**:first_seen/detail/两 router 链接/note/exempt——盲审 A-P2 少记两条)
- 表体 el-table→el-table-v2(≤500 行;现 8 列全迁:含 handled_by/note 落点明示;操作列 h(ElButton) 处置三钮);脱敏分支(count/aggregated 摘要,同 Risk 页范式)

## 验收
1. build 绿+冒烟绿;DslEditor 输入 `mea` 弹补全(手验路径描述);DSL 保存/试算不回归
2. dist 含 cjk 子集;禁系统字体(devtools)后中文仍正确渲染
3. 6 页 readonly 灰(nav 配 readonly 态本地验);Reconcile 抽屉+虚拟滚动+脱敏分支
4. pytest 全绿(后端零改动预期;若字体脚本入库则 node 侧无测试,验收靠 build 产物)

## 风险
- MiSans 直链失败率高 → Noto 替代路径保底(决策记录)
- cn-font-split 产物体积失控(全字符集)→ 脚本限定 subset(常用简中 3500+ASCII);CI/构建不阻塞(产物预生成入库,非每次 build)
- v2 Reconcile 处置按钮 cellRenderer(事件闭包)复杂度——操作列保 el-button 组件渲染
