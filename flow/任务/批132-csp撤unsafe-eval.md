# 批 132 · CSP 撤 unsafe-eval（vue-i18n 构建期预编译）

> 立项 2026-10-21（用户裁定 A：现在立项）。起源=10-12 凌晨 CSP 登录页白屏事故的应急修复遗留（详见 `flow/待办.md` CSP 书签行与当日进展）。

## 问题

10-11 安全评估上线 `script-src 'self'`，10-12 凌晨登录页白屏：vue-i18n **运行时编译词条**（内部 `new Function`）被拦。应急修复=`script-src` 加 `'unsafe-eval'`（repo `f21ad67` 两处 conf + 线上热修）。

**遗留风险**：`'unsafe-eval'` 使 CSP 的 XSS 纵深缺一角——外源脚本仍被 `'self'` 挡，但同源注入（存储型 XSS 进自家数据）可直接 eval 利用。安全评估上线时的最严姿态缺这一角。

## 正解

vue-i18n **构建期预编译**（`unplugin-vue-i18n`）：词条在 vite build 时编译成函数，运行时**不需要** `new Function` ⇒ 撤 `'unsafe-eval'` ⇒ 恢复最严 CSP。

## 产出（v1）

1. **vite 配置加 `unplugin-vue-i18n` 插件**（`web/vite.config.js`）：SFC 内 `<i18n>` 块与语言 JSON 词条构建期编译。
2. **撤 `'unsafe-eval'`**：`server/scripts/nginx/quant.conf` 模板两处（f21ad67 的位置）——**prod 生效需 michael 通道改 nginx conf + reload**（repo 模板先行，线上随批）。
3. **回归验证（headless Chrome，10-12 事故教训——curl 不可用）**：登录页（未登录态=事故原现场）+ 主导航至少 3 页 + 语言切换（zh/en）——DOM 完整性 + console **零 CSP 违规**。
4. 验证通过前**不撤线上**（先 build 产物自证，再动 conf）。

## 限定范围
改：`web/vite.config.js`、`web/package.json`（+unplugin-vue-i18n 依赖）、`server/scripts/nginx/quant.conf`、`tests/`（如需）。不碰：词条 JSON、i18n 调用面、后端。

## 验收标准
1. `npm run build` 绿，产物无 `new Function`（grep 产物验证）
2. headless Chrome：登录页 DOM ≥ 事故后基线（4231B）+ console 零 CSP 违规
3. 语言切换 zh/en 正常（预编译后词条渲染一致）
4. nginx conf 模板撤 unsafe-eval；**线上生效需 michael 通道 reload**（用户步骤）
5. 撤后 headless Chrome 复验（ CSP 头确认无 unsafe-eval + 页面完整）

## 风险
- 词条含动态 key（`t(variable)`）时预编译需 legacy 模式兼容——build 期若报错则按插件文档处理（有先例文档）
- 失败兜底=回滚 vite 配置（unsafe-eval 保持现状，零损失）

## mock 方式
无 DB 依赖；headless Chrome 本机起 dev server 或对 staging 验证。
