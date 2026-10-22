# 批 132 · CSP 撤 unsafe-eval（vue-i18n 构建期预编译）· 已收官

> 立项 2026-10-21（用户裁 A）。**2026-10-22 收尾完成**：dist 先行（`202610221540`）→ 线上 conf 撤 eval → 公网真浏览器复验全绿。
> 过程（4 P0 + 4 P1 双盲审处置）见 git 历史 `840ef2b`（v2）/ `4719761`（v3）。

## 背景

10-12 事故：`script-src 'self'` 拦 vue-i18n 运行时消息编译器（`new Function`）⇒ 登录页白屏；应急加 `'unsafe-eval'`。本批构建期预编译撤销它。

## 落地（全链）

1. `web/package.json`：`@intlify/unplugin-vue-i18n@11.2.7`（钉死）+ `vue-i18n` 精确 `9.14.4`（JIT 是「插件产 AST ↔ 运行时认 AST」的私有契约，小版本错位即白屏）+ `verify:csp` script。
2. `web/vite.config.js`：`VueI18nPlugin`（ESM `import.meta.url` 形态 + `dropMessageCompiler`）+ `define.__INTLIFY_JIT_COMPILATION__` + `manualChunks` 拆 `locales`。
3. `web/src/locales/index.js`：**11 条**词条转义（`{}` / `@` / `|`）——含 `backtest.groupAvg` 的 `|`（复数分隔符：build 绿但**静默丢前三段**，比硬错更危险）。
4. `web/scripts/check-locales.mjs`：第 5 门（AST 校验保留符未转义即红）——防手抄清单再漏（本次 9→11 即证）。
5. `web/scripts/verify-csp.mjs`：静态服务带**生产同款 CSP** + `securitypolicyviolation` 事件 + `pageerror`，zh/en 双语言。
6. `server/scripts/nginx/quant.conf`：3 处 `script-src` 撤 `'unsafe-eval'`（**30 / 91 / 102 行，与线上 `/etc/nginx/conf.d/quant.conf` 逐行一致**）。

## 验收证据（2026-10-22）

- 本地：`npm run build` 全链绿（prebuild 五门）；产物 `vue-vendor` / `locales` **零 `new Function`**；严格 CSP 下 zh/en 登录页 0 违规 0 错误。
- 线上（conf 撤后，公网真浏览器）：`/login` zh/en **0 violation / 0 pageerror**，表单完整、文案正确。
- 线上登录后 **12 路由 × 2 语言 = 24 项全 PASS**（`/` `strategy` `backtest` `factors` `pool` `screener` `integrations` `permissions` `risk` `settings` `dataops` `observe`）。
- 响应头三路径（`/` `/index.html` `/assets/*`）均只剩 `script-src 'self'`。

**唯一已知违规（可接受）**：`echarts-*.js:61` 的 custom-element 能力探测用 `new Function`（外层 `try/catch`）⇒ 图表页触发 1 条 `script-src eval` violation。CSP 拦下它本身就是防护生效，页面与功能不受影响；复验判据把它单列白名单，其余违规一律算失败。

## 回滚

conf 一行加回 `'unsafe-eval'` + `systemctl reload nginx`（快于回滚 dist）。
**逆向不变量**：撤 eval 后若要回滚 dist 到 132 之前版本，**必须先恢复 eval**（旧 bundle 含运行时编译）。