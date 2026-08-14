# Web 前端设计规范（单一真相源）

> 本文件是前端 UI 一致性的**规则来源**。新增页面/组件必须遵守；改规则先改这里。
> 一致性由**全局机制**保证（见 §1），不靠各页面逐个写属性。

## 1. 全局机制（代码只写一处）

| 机制 | 位置 | 控制什么 |
|---|---|---|
| `<el-config-provider size="default">` | `src/App.vue` | **全站所有 Element 组件的尺寸**（按钮/输入框/选择器/表格/标签/开关…）。改这一个值 = 全站变。页面**不得**再写 `size` 属性 |
| 全局字体 | `src/App.vue` body 样式 | 字体家族统一 'Segoe UI', Roboto, sans-serif |
| 文案 | `src/locales/index.js`（i18n） | 所有用户可见文案走 `t()`，zh/en key 1:1 |
| 条款文本 | 后端 `server/src/web_api/terms.py`（`GET /api/terms`） | 注册页与开通邮件共用单一源 |

## 2. 尺寸规范

- **默认档（default）**：全站统一（按钮 32px / 输入框 32px / 表格行高 40px+ / 标签 24px 高 14px 字）。由 ConfigProvider 全局生效。
- **large**：仅鉴权页（登录/注册/找回/重置）的全宽提交 CTA 与大输入框。
- **small**：已废弃，不再使用。
- 宽度随内容自适应；不写固定宽（表单控件需限宽用 `style="width: Npx"` 按需）。

## 3. 按钮规范

| 属性 | 规则 |
|---|---|
| 字体家族/字号/padding/高度 | 跟随全局 default 档，不单独设置 |
| 宽度 | 随内容；CTA 场景可 `width:100%` |
| `link` / `plain` / `text` | **禁止使用**（可点击必须一眼像按钮） |

### 颜色语义（唯一规则）

| type | 用途 | 示例 |
|---|---|---|
| `primary` 蓝 | 主操作 + 常规操作（新建/提交/编辑/刷新/搜索/取消/测试/详情/返回/重置…） | 邀请开通、新建策略、编辑 |
| `success` 绿 | 启动/启用 | 启动策略、启用 |
| `warning` 橙 | 停止/禁用/回补/检测 | 停止、禁用、回补、卡死检测 |
| `danger` 红 | 删除/终止/解绑/熔断 | 删除、终止、一键熔断 |

- 每个按钮**必须**有 type（不写 type 的灰按钮已废止——观感像禁用）。
- 禁用态用 `:disabled`；原因用 `title` 悬停提示，不改按钮文字。

## 4. 颜色系统（Element 语义色）

- 状态展示统一用 `el-tag` + 语义 type：`success`=运行/已启用/成功 · `warning`=已停/部分 · `danger`=错误/已禁用 · `info`=只读/未验证/中性。
- 语义色不自定义十六进制，直接用 Element 默认（primary #409eff 等），保证全站一致。

## 5. 表格规范

- `stripe` 斑马纹；尺寸跟随全局（不写 size）。
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

## 9. 文字链例外清单（仅这些允许非按钮可点击）

1. 备案号（Footer，工信部外链惯例）
2. 注册页条款链接（勾选框内嵌行内链接）+「返回登录」等鉴权页跳转（整页语境）

其余一切可点击元素必须是按钮。

## 10. i18n 规范

- 全部文案走 `t('namespace.key')`；新增 key zh/en 同步（对齐脚本验证，当前 725 key 双语 1:1）。
- 占位符 `{n}/{name}` 双语一致；代码注释、console、法律编号不翻译。
