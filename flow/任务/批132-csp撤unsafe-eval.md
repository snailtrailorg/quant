# 批 132 · CSP 撤 unsafe-eval（vue-i18n 构建期预编译）· v3

> 立项 2026-10-21（用户裁 A）。**v3（双盲审齐）**：A 审 v1 出 4 P0（实证）；B 审 v2 再出 2 P0 + 4 P1（归因纠正）。全部处置。

## 背景

10-12 事故：`script-src 'self'` 拦 vue-i18n 运行时消息编译器（`new Function`）⇒ 登录页白屏；应急加 `'unsafe-eval'`。本批构建期预编译撤销它。

## P0（6 条，全实证）

### P0-1 **11 条**词条含保留符（v2 写 9 条，B 补 2）
`{}`/`@` 是硬错（build 直接失败）；**`|` 是复数分隔符 —— build 绿、静默腐化**（只显示最后一支）。

| 词条 | 位置 | 保留符 | 后果 |
|---|---|---|---|
| zh.backtest.jsonHint / factors.paramsInvalid / factors.phParams / err.IFACE_PARAMS_INVALID / err.EMAIL_INVALID_FORMAT | locales/index.js | `{}`/`@` | build 硬错 |
| en.backtest.jsonHint / factors.phParams / err.IFACE_PARAMS_INVALID / err.EMAIL_INVALID_FORMAT | 同上 | `{}`/`@` | build 硬错 |
| **zh.backtest.groupAvg（:491）/ en（:1843）** | 含 4 个 `|` | `|` | **build 绿但上线丢前三段**（BacktestRun.vue:40 实测只显示「夏普 3」） |

修法：转义 `{'{'}`/`{'}'}`/`{'@'}`/`{'|'}`（实测渲染正确；不影响 check-locales 占位符正则）。`$` 在 vue-i18n 9 非保留符（词法只认 `{ } @ |`），不处理。
**防复发**：`web/scripts/check-locales.mjs` 加第 5 门（AST 遍历校验消息 token 合法性）——手抄清单必漏（本次 9 vs 11 即证）。

### P0-2 插件默认配置 = build 绿但运行时白屏
插件 v11 产 AST，vue-i18n 9.14.4 运行时需 `__INTLIFY_JIT_COMPILATION__` 才认；未 define ⇒ SyntaxError + 登录页空壳。
修法：`define: { __INTLIFY_JIT_COMPILATION__: 'true' }` + `VueI18nPlugin({ include: <glob>, dropMessageCompiler: true })`。

### P0-3 验证必须自建「生产 CSP 静态服务」，且**分两档**
- dev server 用 full build 不复现；`vite preview` 不发 CSP 头 ⇒ 零证据。
- **B 补 P0**：静态服务无后端 ⇒ 3 个动态 key 页（需 admin/数据）进不去，router 重定向回 /login，脚本「通过」=假绿。

**两档**：
- **无后端档**：静态 CSP 服务 + fresh context ⇒ 只测 `/login` 的 0 violation / 0 pageerror。
- **有后端档**：静态服务挂 `/api`+`/ws` proxy → 127.0.0.1:8000（照抄 vite.config.js:15-22）+ 后端起（dev-start.sh）+ 登录（verify-gate.mjs 式 SMOKE_PASS）⇒ 再跑动态 key 页/菜单页。
- 实现细节：module script 必须 `Content-Type: text/javascript`（回 text/plain ⇒ 整页空白=假红）；SPA history 回退；CSP 只加文档响应（子资源头不参与执行策略）。
- **zh 分支必须强制**：fresh context + headless 默认 en-US ⇒ detectBrowserLang 恒 en，zh 永不执行。用 `evaluateOnNewDocument` 覆写 `navigator.language/languages` 或写 `localStorage.lang`（verify-gate.mjs:10 现成写法）。

### P0-4 上线次序 + **逆向不变量**
① 先上 dist（conf 仍允许 eval）→ 线上验 zh/en + 关键页；② 再改 conf → `nginx -t && systemctl reload nginx`；③ 复验头无 unsafe-eval + 页面完整。
**逆向不变量（B 补）**：conf 撤 eval 后，**任何回滚 dist 到 132 之前版本都会二次白屏**（旧 vue-vendor 含 new Function）⇒ 回滚 dist 前**必须先恢复 eval**。写进任务。
**真源（B 补）**：deploy 全链无 nginx 任务（14 只 wrapper 无 nginx）⇒ 手工通道；且仓内 `server/scripts/nginx/quant.conf` 只落 releases/<id>/scripts/nginx/，**不是 /etc 那份**（10-12 是热修）⇒ 动手前先 `nginx -T` 快照 diff 三行再改。
**dist 走管道**：release.yml:120 preflight build / :301 阶段2 rsync dist / :517 flip-web ⇒ 走 release.yml + 部署窗，禁手工 scp。

### P0-5 两处落地即炸（B 补）
- `web/package.json` 有 `"type": "module"` ⇒ vite.config.js **无 `__dirname`** ⇒ 用 `fileURLToPath(new URL('./src/locales/**', import.meta.url))`。
- **vue-i18n 精确钉 9.14.4**（现 `^9.13.0` 可漂移）——JIT 是「插件产 AST ↔ 运行时认 AST」私有契约，小版本错位=白屏。

### P0-6 体积真实数字（B 纠正归因）
3.9MB/1MB gzip 那个 chunk 是 **monaco**（编辑器页动态引），**不是主包**。entry = 148KB 裸 / **60KB gzip**，locale 就在 entry ⇒ 预编译后 entry **60KB → ~115KB gzip（+90%）**。
修法：`manualChunks` 增 `if (id.includes('/src/locales/')) return 'locales'`（vite.config.js:38 起）——hash 稳定 ⇒ 二次访问 immutable，index.html 并行 preload。验收记 entry 前后两个 gzip 数。

## P1

- CSP **3 处**一起改（:30/:91/:102）。
- 「产物无 new Function」判据改：**除 echarts 已知 2 处（import 期 custom-element 探测，try/catch 吞）外全 assets 0 处** + 运行时违规=0（否则别的库引入 eval 无感）。
- 动态 key 页断言：先 waitFor 页面特征文案再断 0 violation（空页也 0 violation）。
- 覆盖建议从「3 页」扩成**登录后主菜单 16 项全扫**（顺带覆盖 monaco 页，成本≈0）。
- prebuild 是**四项**（check-tokens/check-locales/test-coldiff/check-orphans），转义后行宽变化可能触 coldiff ⇒ 改完跑全链。
- 加 `"verify:csp"` npm script（否则没人跑）。

## 落地清单

1. `web/package.json`：`@intlify/unplugin-vue-i18n@11.2.7`（钉死）+ **vue-i18n 精确 9.14.4** + `verify:csp` script + lock
2. `web/vite.config.js`：VueI18nPlugin（ESM include 写法 + dropMessageCompiler）+ define JIT flag + manualChunks 拆 locales
3. `web/src/locales/index.js`：**11 条**转义（含 groupAvg 的 `|`）
4. `web/scripts/check-locales.mjs`：加第 5 门（保留符校验，防复发）
5. `web/scripts/verify-csp.mjs`：两档（无后端/有后端）+ securitypolicyviolation + pageerror + zh 强制
6. `server/scripts/nginx/quant.conf`：3 处删 unsafe-eval（线上以 `nginx -T` 快照为准）

## 验收标准
1. `npm run build` 全链绿（四门 prebuild）
2. 无后端档：`/login` × zh/en ⇒ 0 violation / 0 pageerror / 选择器齐
3. 有后端档：登录成功 → 主菜单 16 项全扫 + 7 处动态 key 页 ⇒ 0 违规（echarts 除外）+ 文案渲染正确（groupAvg 四段齐全）
4. entry gzip 前后对比记录（预期 ~60KB → ~115KB，拆 locales 后回落到 ~65KB）
5. 上线四步 + 逆向不变量 + `nginx -T` 快照 diff 留档
