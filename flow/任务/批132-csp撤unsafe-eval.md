# 批 132 · CSP 撤 unsafe-eval（vue-i18n 构建期预编译）· v2

> 立项 2026-10-21（用户裁 A）。**v2（盲审 A 实证后重写）**：4 P0 + 4 P1 全处置。v1 有两处必炸（build 直接失败 / build 绿但运行时白屏）+ 验证假绿 + 上线次序缺失。

## 背景

10-12 事故：`script-src 'self'` 拦 vue-i18n 运行时消息编译器（`new Function`）⇒ 登录页白屏；应急加 `'unsafe-eval'`（f21ad67 + 线上热修）。本批构建期预编译撤销它。

## 现状（A 实核）

- 词条：单文件 `web/src/locales/index.js`（160KB 静态 JS 对象）；**无 SFC `<i18n>`、无 tm/rt、无 setLocaleMessage**——插件最干净接入形态。
- 初始化：`web/src/i18n.js:24-29` `legacy: false`，66 处 `useI18n()`（全线 composition，legacy 问题不存在）。
- 现状产物：`dist/assets/vue-vendor-*.js` 含 `new Function`（=事故同款）。
- CSP 配置实为 **3 处**（quant.conf :30 server 级 / :91 index.html / :102 assets），非 v1 写的 2 处。

## P0（必炸项，v2 已修）

### P0-1 构建直接失败：9 条词条含 `{}`/`@` 保留符
`zh.backtest.jsonHint`、`zh.factors.paramsInvalid`、`zh.factors.phParams`、`zh.err.IFACE_PARAMS_INVALID`、`zh.err.EMAIL_INVALID_FORMAT` + en 同 4 条（共 9）。
根因：vue-i18n 消息语法里 `{`/`}`/`@` 为保留符（运行时只 warn 回落，构建期硬错 `Invalid token in placeholder`）。
**修法（已实证渲染正确）**：转义为 `{'{'}` / `{'}'}` / `{'@'}`。**限定范围必须放开这 9 条**（只改这 9 条）。
附带：`strictMessage` HTML 检查无风险（词条值零 HTML 标签）。

### P0-2 默认配置 = build 绿但运行时白屏（最危险）
插件 v11 把资源编译成 AST，而 vue-i18n **9.14.4** 运行时要 `__INTLIFY_JIT_COMPILATION__` 才认 AST；未 define ⇒ 运行时把 AST 当字符串再编译 ⇒ `SyntaxError` + 登录页空壳（dev 用 full build 看不出）。
**修法（已实测）**：
```js
define: { __INTLIFY_JIT_COMPILATION__: 'true' },
plugins: [vue(), VueI18nPlugin({ include: '<abs>/src/locales/**', dropMessageCompiler: true })],
```
实测：产物 `new Function`/`Function(`/`eval(` 均 0；严格 CSP 下渲染 `保存|你好，Bob！`，零违规零 pageerror。
备选（同样实测干净，但属依赖大版本）：升 vue-i18n@11.4.16 —— 另批，本批走 A。

### P0-3 验证必须自建「生产 CSP 静态服务」
- dev server 用 full build ⇒ P0-2 类故障不复现；
- `vite preview` **不发 CSP 头** ⇒ 零证据（=curl 盲区换壳）。
**必须**：`vite build` → node http 起静态服务（**带生产同款 CSP 串** + history 回退）→ puppeteer-core + `/usr/bin/google-chrome`（仓内先例 `web/verify-gate.mjs`）：
1) `evaluateOnNewDocument` 注册 `securitypolicyviolation` 收集（console 文本不可靠）；
2) 全新 context（不写 localStorage）= 未登录态 → `/login`；
3) 收 `pageerror`；
4) zh/en 各跑一遍。
**判据**：`violations.length === 0 && pageerrors.length === 0` + 选择器断言（`input[type=password]`、按钮文案、`#app` 非单注释）。原「DOM ≥ 4231B」降为辅助信号。

### P0-4 上线次序（否则第二次白屏）
nginx conf 无 ansible 处置 = michael 手工通道，与 dist 发布是**两次独立操作**。次序：
① **先上 dist**（此时 conf 仍允许 eval）→ 线上 headless 验 zh/en + 关键页；
② 再改 conf + reload；
③ 复验响应头无 `unsafe-eval` + 页面完整。
回滚 = 一行加回 `'unsafe-eval'`（快于回滚 dist）。

## P1

- **CSP 3 处一起改**（:30/:91/:102）——只改 2 处虽能生效（子资源 CSP 不参与文档执行策略），但留漂移。
- **「产物无 new Function」判据改口径**：`echarts-*.js` 有 `new Function("return ("+r+")")()` 死分支（JSON.parse 兜底，实测不执行）⇒ 判据 = 「**vue-vendor 内 0 处** + 运行时违规=0」。
- **体积实测（须记录）**：词条 128KB → **1.31MB 裸 / 114KB gzip（+63KB）**；主包已 3.9MB/1MB gzip。接受或拆 locale chunk——任务文件留数字，别上产后才发现。
- **版本钉死**：`@intlify/unplugin-vue-i18n@11.2.7 -D` + 提交 package-lock（v12 行为变了）。

## P2

- `include` 用 glob `src/locales/**`（别硬路径；别把应用源码纳入）。
- **动态 key 页必须验**（预编译后是对象树查找，安全但需覆盖）：`PermResources.vue:133`（nav.${}）/ `Risk.vue:132`（common.lvl.${}）/ `InterfacesCard.vue:37`（interfaces.cap_${}）/ `RiskRules.vue:90` / `AlertSettings.vue:152` / `utils/status.js:14` / `utils/cronText.js:74`。
- 语言切换断「渲染一致」（zh/en 同页 innerText 含对应语种字样），非「不报错」。
- 转义后行宽变化可能触 `check-coldiff` ⇒ 改完跑全链 `npm run build`（含 prebuild 三检查）。

## 最小改动清单

1. `web/package.json`：`@intlify/unplugin-vue-i18n@11.2.7`（devDep 钉死）+ lock
2. `web/vite.config.js`：VueI18nPlugin（include glob + dropMessageCompiler）+ define JIT flag
3. `web/src/locales/index.js`：9 条词条转义
4. `server/scripts/nginx/quant.conf`：3 处删 `'unsafe-eval'`
5. 验证脚本：`web/scripts/verify-csp.mjs`（落库作回归守门）

## 验收标准
1. `npm run build` 全链绿（含 prebuild 三检查）
2. verify-csp.mjs：登录页 + 3 个动态 key 页 × zh/en → 0 violation / 0 pageerror / 选择器齐
3. `vue-vendor` 产物 0 处 `new Function`；体积变化记录在案
4. **上线次序**：dist 先行（eval 仍开）→ 线上验 → conf 撤 eval（michael）→ 复验头 + 页面
5. 回滚预案：conf 一行加回
