# Web 前端设计规范（单一真相源）

> 本文件是前端 UI 一致性的**规则来源**。新增页面/组件必须遵守；改规则先改这里。
> 一致性由**全局机制**保证（见 §1），不靠各页面逐个写属性。

## 1. 全局机制（代码只写一处）

> ⚠️ **字体/字号与语义色已由 `src/styles/tokens.css` 取代（wd-04 设计系统 + wd-14 方案）**——字体走 `var(--font-ui)` 中文栈（含 MiSans 回退），字号走 wd-14 §2.1 三点校准 clamp（@1280/@1707/@1920，单一驱动源 `--fs-body`），语义色走四令牌四色相（`--up/--down/--success/--critical`），EP 整组 ramp 同覆写。本节字体行仅为历史参考。

| 机制 | 位置 | 控制什么 |
|---|---|---|
| `<el-config-provider size="default">` | `src/App.vue` | **全站所有 Element 组件的尺寸**（按钮/输入框/选择器/表格/标签/开关…）。改这一个值 = 全站变。页面**不得**再写 `size` 属性 |
| ~~全局字体~~ → **tokens.css** | `src/styles/tokens.css` `--font-ui` / `--font-num` | 字体家族+等宽数字+@font-face（JBMono Web 预生成）|
| 文案 | `src/locales/index.js`（i18n） | 所有用户可见文案走 `t()`，zh/en key 1:1 |
| 条款文本 | 后端 `server/src/web_api/terms.py`（`GET /api/terms`） | 注册页与开通邮件共用单一源 |

## 2. 尺寸规范

- **默认档（default）**：全站统一（按钮 32px / 输入框 32px / 表格行高 40px+ / 标签 24px 高 14px 字）。由 ConfigProvider 全局生效。
- **large**：仅鉴权页（登录/注册/找回/重置）的全宽提交 CTA 与大输入框。
- **small**：紧凑数据区专用档——表格单元格内嵌的按钮/输入/标签、`el-descriptions`、KPI 附注、密集筛选行用 small；页面主体操作钮与表单控件仍 default。（2026-09-03 改文就码：原"已废弃"与代码实况不符，wd-19 漂移项；口径=views+components 93 处，全站含 layouts/render 函数约 101）
- 宽度随内容自适应；不写固定宽（表单控件需限宽用 `style="width: Npx"` 按需）。

## 3. 按钮规范

| 属性 | 规则 |
|---|---|
| 字体家族/字号/padding/高度 | 跟随全局 default 档，不单独设置 |
| 宽度 | 随内容；CTA 场景可 `width:100%` |
| `link` | **禁止使用** |
| `plain` / `text` | 仅限**紧凑数据区次要动作**（卡片头"更多"、快捷区间/模板行、表格行内动作）——主体操作钮禁用（text 10 处 + plain 1 处既定例外，2026-09-03 改文就码） |

### 颜色语义（唯一规则）

| type | 用途 | 示例 |
|---|---|---|
| `primary` 蓝 | 主操作 + 常规操作（新建/提交/编辑/刷新/搜索/取消/测试/详情/返回/重置…） | 邀请开通、新建策略、编辑 |
| `success` 绿 | 启动/启用 | 启动策略、启用 |
| `warning` 橙 | 停止/禁用/回补/检测 | 停止、禁用、回补、卡死检测 |
| `danger` 红 | 删除/终止/解绑/熔断 | 删除、终止、一键熔断 |

- 每个按钮**必须**有 type（不写 type 的灰按钮已废止——观感像禁用）。
- 禁用态用 `:disabled`；原因用 `title` 悬停提示，不改按钮文字。
- **el-link 文字链例外**（2026-09-03 改文就码收录，wd-19 漂移项）：`el-button` 的 `link` 仍全站禁用（0 违例）；`el-link` 仅限**数据上下文内嵌跳转**（表格单元格数值/行内引用计数跳转），现存 6 处既定例外——Trading 持仓 symbol 跳个股、Reconcile 订单流/持仓快照 ×2、DataManage 调度表达式、Factors 引用数 ↗、SmtpCard 查看发件箱。鉴权页跳转走 §9 清单，不在此列。

## 4. 颜色系统（Element 语义色）

> ⚠️ **本节已被 tokens.css 取代（wd-04 设计系统 + wd-14 方案）**——品牌色 #1F4FD8（替换 EP 默认蓝 #409EFF）+ EP 整组 ramp + 四令牌四色相（--up 红涨/--down 绿跌/--success 青绿/--critical 深红）+ 暗色变体。本节仅为历史参考，新页面一律用 `var(--*)` 令牌。

- 状态展示统一用 `el-tag` + 语义 type：`success`=运行/已启用/成功 · `warning`=已停/部分 · `danger`=错误/已禁用 · `info`=只读/未验证/中性。
- 语义色不自定义十六进制，直接用 Element 默认（primary #409eff 等），保证全站一致。

### 4.1 换色 / 换令牌操作（2026-09-03 令牌尾批后定）

令牌化完成后，换配色 = 改 `tokens.css`，**代码零改**（`var(--*)` 全站自动跟随）。分两种：

**A. 只换色值**（最常见）：
1. 改 `tokens.css` 里对应变量的值，**`:root` 与 `html.dark` 两处成对改**（漏一处 = 明/暗破相）；
2. `scripts/check-tokens.sh` 基线**不动**（代码内联 hex 字面量数没变）。

**B. 新增/重定义令牌**（换语义档，如加「数据好」档或拆档）：
1. **先**在 `tokens.css` 加定义（+ 暗色变体）——先定令牌、再替换引用，别反过来；
2. 批量替换代码旧引用 → 新令牌；
3. 内联 hex 有增减时 `bash scripts/check-tokens.sh --update` 收紧基线。

**三个注意点**：
- 图表色走 `cssVar('--x')`（`getComputedStyle` 解析，见 `utils/cssVar.js`），换值跟随，但已渲染图表不即时重算（echarts `computed` 惰性求值）；
- 语义色换值可以、换义不行：`--up/--down` 只指涨跌、`--success/--critical` 只指反馈/系统状态（绿=跌仅国内蜡烛图，别处绿色就是普通「好/成功」）；
- 令牌门（`scripts/check-tokens.sh`）只扫 `views/+components/`，内联 px/hex 只许降不许升（基线首采/收紧用 `--update`）。

## 5. 表格规范

- 无斑马纹（wd-04 §4.3 裁定清零，hover 高亮替代；wd-16 验收全站 0 残留——2026-09-03 改文就码，原 stripe 条款为失真残留）；尺寸跟随全局（不写 size）。
- 长文本列（email/URL/备注）：`min-width` + `show-overflow-tooltip`（自适应+省略悬停看全），**不写死 width**。
- 操作列：固定 `width`（按按钮数预留，中英文都不换行），单元格内容 `white-space: nowrap`。
- 大数据量（>1000 行）用 `el-table-v2` 虚拟滚动（SymbolManage 模式）。

## 6. 表单规范

- 窄表单/中英文混排风险高的表单：`label-position="top"`（标签永不换行）。
- 弹窗宽表单：`label-width="100px"+`，label 文案短的可用。
- 密码字段：必带 `show-password`；新密码下方放复杂度提示（`common.passwordRule`）；二次确认字段不一致红框（`.mismatch`）。

## 7. 反馈规范

- 操作结果：`ElMessage` success/error/warning（文案走 i18n，含 `{n}` 等插值）。
- 危险操作确认：`ElMessageBox.confirm(msg, t('common.tip'), { type: 'warning' })`；高危（强制删除）用 `type:'warning'` + 高危标题。
- 后端错误透传：`e?.detail || t('...')`，让用户看到真实原因。

## 8. 布局规范

- 每页一个 `el-card`；页头 `#header` = 左标题 + 右主操作按钮（flex space-between）。
- 统计卡片行：`el-row/el-col` + `.stat`（label 灰小字 / value 大粗体，见 Trading/Dashboard）。
- 分区：`el-divider content-position="left"` + 分区标题。
- 备案号：`Footer.vue` 全局固定底栏（所有页面 viewport 底部，22px），唯一允许的文字链（政府外链）。

## 8.5 弹窗规范（2026-09-02 立法：编辑形态默认弹窗——页面整洁；宽度/交互/页脚三统一）

> 依据：全站 24 处 el-dialog 实测宽度 11 档离散、防误触属性仅 3 处——先立法后迁移，杜绝新一代克隆各异。

1. **编辑形态三档**（替代历史"页面块表单悬底"模式）：默认弹窗；超重表单（含代码/参数编辑器）整页路由；单字段开关类行内直改。
2. **宽度三档**（就近归档，禁止新造宽度）：
   - **S = 420px**：单字段/确认型输入（日期回补、豁免登记、加池）
   - **M = 560px**：标准编辑表单（默认档）
   - **L = 720px**：重表单（参数编辑/多段/内嵌子表：策略、因子、发起回测）
   - 数据展示型例外不受三档管（K 线 90%、条款 80%）
3. **交互纪律**：编辑表单一律 `:close-on-click-modal="false"`（防误触丢稿）；纯查看/展示型不设；嵌套弹窗加 `append-to-body`；ESC 默认开。
4. **页脚**：左=取消（default 灰——全站唯一无 type 例外，"非动作"按钮）；右=主操作（保存/新建 primary，loading 挂它；删除确认 danger；回补/重置类 warning，沿 §3 颜色语义）。
5. **表单**：`label-width="120px"` 纵向禁 inline；L 档内容超高内滚（max-height）。
6. **标题**：成对 i18n 键（`form.id ? 编辑X : 新建X`）；查看型「XX 详情」。
7. **视觉零新增**：字号/圆角/间距全走 EP 主题令牌（dialog title 已挂 `--fs-card`），弹窗不引入新色。
8. **容器页 tab 壳一律 TabsShell**（wd-20 §2.2）：新增带 tab 的容器页禁止手写 el-tabs 壳——统一 `components/TabsShell.vue`（query ?tab= 同步+sessionStorage 记忆+i18nKey label）。

## 9. 文字链例外清单（仅这些允许非按钮可点击）

1. 备案号（Footer，工信部外链惯例）
2. 注册页条款链接（勾选框内嵌行内链接）+「返回登录」等鉴权页跳转（整页语境）

其余一切可点击元素必须是按钮。

## 10. i18n 规范（N 语言设计，en 为缺省）

- **架构约束**：多语言设计为支持任意语言，当前实现 zh/en 两种；**英语是语言不匹配时的缺省**。任何地方不得写死"只有两种语言"的假设。
- **加新语言 = 只加条目，逻辑零改动**：① `locales/index.js` 加语言对象 ② `i18n.js` 的 `LANGUAGES` 注册一项 ③ 后端 `terms.py` 的 `TERMS`/`LANG_NAMES` 加条目 ④ 邮件模板 dict（`email_service`）加语言条目。遍历/检测/回落逻辑全部注册表驱动，不感知具体语言。
- 全部文案走 `t('namespace.key')`；新增 key 所有语言同步（对齐脚本验证，当前 735+ key 各语言 1:1）。
- 占位符 `{n}/{name}` 各语言一致；代码注释、console、法律编号不翻译。
- 浏览器语言检测：`i18n.js` 遍历 `navigator.languages` 匹配已实现语言，不匹配回落 en。
- 邮件语言：跟随操作者当前界面语言（前端随请求传 `lang`，后端 `normalize_lang` 未匹配回落 en）。
- 条款（页面弹窗 + 邮件）：**全语言纵向堆叠**（`get_terms_items()` 遍历），不依赖检测。
- **错误提示（错误码化）**：后端用户流程错误统一 `ApiError(status, CODE, 中文兜底)`（响应 `{detail, code}`）；前端 `apiErr(e)` 优先显示 `err.<CODE>` 本地化映射、无映射回落 detail。加新错误 = 后端 raise 处定码 + locales `err` 命名空间加各语言条目。深层管理接口尚未迁移完的可增量补码，未映射自动回落不破坏。
