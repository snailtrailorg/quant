# W4 权限三维化·C 阶段:override 编辑器+三维矩阵 UI+玻璃盒增强(2026-09-01 拉前;完美系统战役第四批)

> 排期:plan.md 战役表(原 09-05)。10 号 §4 管理界面+§6 C 阶段。数据敏感级**执行面**(持仓聚合/risk_log 计数脱敏)归 W5 与 D 同批——本批交付三维**编辑与解析链**,执行面只接 nav(菜单)与 api(现成)。

## 现状基线(2026-09-01 源码)
- permission 表三维就绪(0056:subject_type=role|user × dimension=nav|api|data × effect=allow|deny),**但仅 (role,api) 被消费**(load_role_permissions auth.py:65;require_perm/:auth/me/管理页三消费点)
- 管理页 Permissions.vue=13 api 键平铺(GET/POST /api/permissions 全量重写 role allow 集)
- 玻璃盒=骨架(myperms 弹窗列 permission tag,无来源/无 override 维度)
- **菜单驱动是虚报**(盲审 A-P0 实锤):MainLayout.vue:252 `loadPerms()` **全仓无定义**——setup 运行即 ReferenceError,**现产前端自 09-01 08:05(1676e0e)登入后白屏**;菜单实际仍 role v-if(L34/37/39)。build 绿+API 冒烟均不可见(Vue 运行时错)——W2 冒烟门 rationale 活案例
- **"系统策略键现锁定"是幻觉**(盲审 B-P0):require_perm 全表驱动(auth.py:120 集合 contains,表空才回退字典)——halt/resume 等键今天就能被角色全量重写关掉;锁**必须 W4 新建**且 role 重写+user override **双路径同锁**,只锁一边=虚设

## 产出 1:后端三维解析链+override CRUD

- **文件**:`src/web_api/auth.py`+`src/web_api/routes/auth_routes.py`
- `load_role_permissions()` → `load_effective_permissions(username, role)`:**user deny > user allow > role allow > 默认拒**(10 号 §3 合并序);user 维读失败 **fail-open=按角色**(盲审 A-P1:user 维无字典可回);缓存键带 username,**invalidate 保持全局清**(盲审 B-P1:现实现即全局清+单 worker 写后即生效,勿改按键清留 role 脏键)
- `require_perm`/`/auth/me` 换 effective 版;**require_perm 改用 DB role**(盲审 A/B:现取 JWT role,降级后存量 token 24h 仍过检——verify_jwt 已查 users 行,顺带取 role 零成本,与 /auth/me 口径统一)
- **GET /api/permissions 扩展**(admin):返回 `{api:{keys,roles:{}}, nav:{items,roles:{}}, data:{fields,roles:{}}, user_overrides:[...]}`;nav **建模修正**(盲审 A-P0-2):**resource=菜单 id+effect=三态值**(hidden|readonly|readwrite)——原案"resource 存三态"在唯一键 (subject,dimension,resource,effect) 下每 subject 仅容 3 行不可表示;nav items=后端常量(菜单 v2.1 四组 16 项),前端从 GET 拿(**MainLayout 菜单模板同步改消费此清单**——硬编码第二份漂移收编,盲审 A-P2);**subject_id 定死 username**(盲审 A/B-P1:0056 注释 user_id 弃——软删改名仅孤儿行无害);data fields=市场域+敏感级三档
- **POST /api/permissions/user/{username}**(admin):body {dimension, resource, effect ∈ allow|deny|clear}——clear=删该行;写入带 updated_by 审计;invalidate **全局清**(同上)
- **POST /api/permissions/{role}** 扩 dimension 参数(缺省 api 兼容现前端);nav/data 同 api 的 delete-then-insert 全量重写模式,行=resource(菜单id/数据域id)+effect
- **系统安全策略锁新建**(盲审 B-P0):锁键清单={user_mgmt,resume,account_keys}(提权链+自损链高危键)——**双路径同锁**:POST role 重写与 POST user override 均拒编辑锁键(UI 🔒+后端 400 PERMISSION_KEY_LOCKED);**这是行为变更**(现可编辑 halt/resume,锁后不可——release note 声明)+**自锁防线**(盲审 B-P1:admin 对自己/admin 角色 deny user_mgmt 类自损操作拒)

## 产出 2:Permissions.vue 三维矩阵重写(10 号 §4 原型)

- 顶部:`[角色 ▾][用户 ▾]` 双选择器——选角色=编辑角色基线;选用户=override 编辑(只显 override 行+clear 按钮+新增 override)
- 三个 tab:**① 菜单/页面** 16 项 × 三态点选(hidden/readonly/readwrite);**② API** 13 键 × 读/写开关(api 键现语义=开关;系统策略键标 🔒 不可关);**③ 数据** 市场域勾选+敏感级三档
- 顶部常驻提示条:"后端强制 · 菜单只是显性化 · 变更全程审计"
- 保留现 13 键 API 管理能力(tab ② 即现功能迁移,不丢;锁键标 🔒 不可关)
- **loadPerms 白屏修复收编**(产出 0,最高优先):实现 loadPerms(fetch /auth/me→perms ref)+菜单 v-if 从 role 切 permissions 消费——15号批四虚报的真正落地

## 产出 3:玻璃盒增强(10 号 §4 配套)

- `/auth/me` 增 `perm_sources`:每 permission 标来源(role-base/user-override)+user deny 行显式列出;**不含 updated_by/审计字段**(盲审 A-P2:管理员身份不漏给普通用户)
- MainLayout myperms 弹窗:分组渲染(角色基线/个人覆盖/被拒项)+规则来源列

## 不做的(W5+)
- data 维度**执行面**(持仓聚合/risk_log 计数脱敏——动业务端点,与 D 阶段同批)
- 账户域细粒度/市场域 per-user 分配执行面(D 阶段)
- nav 三态的**路由守卫消费**(readonly 态拦截写操作——W5;本批 readonly 只影响 UI 呈现)

## 验收
1. pytest 全绿(**先锁现行为的基线测再重构**——盲审 B:解析链现测试=0,裸改无红网;+effective 解析序测试:user deny 压 role allow/user allow 补 role 缺/nav 三态 CRUD/override clear/空集防线/锁键双路径拒/自锁拒)
2. 本地:管理员建 override(deny trader 用户某键)→该用户 /auth/me permissions 不含该键+perm_sources 标注;撤 override 恢复
3. Permissions.vue 三 tab 可编辑可保存(角色基线+用户 override 双路径);现 13 键功能不回归
4. 玻璃盒:分组+来源可见;viewer/analyst 自查无异常
5. smoke+build 绿

## 风险
- **白屏修复是本批最高优先**(现产带病运行——凡今日有人打开 web 即见;冒烟门盲区=前端运行时,记入 W5 冒烟门范围扩展议题)
- nav 16 项清单前后端同源:后端常量定义,前端从 GET 拿(不硬编码第二份)
- 用户 override 误锁 admin:系统策略键(resume/user_mgmt 等)override 拒绝编辑(UI 锁+后端校验双防线)
