# W4 权限三维化·C 阶段:override 编辑器+三维矩阵 UI+玻璃盒增强(2026-09-01 拉前;完美系统战役第四批)

> 排期:plan.md 战役表(原 09-05)。10 号 §4 管理界面+§6 C 阶段。数据敏感级**执行面**(持仓聚合/risk_log 计数脱敏)归 W5 与 D 同批——本批交付三维**编辑与解析链**,执行面只接 nav(菜单)与 api(现成)。

## 现状基线(2026-09-01 源码)
- permission 表三维就绪(0056:subject_type=role|user × dimension=nav|api|data × effect=allow|deny),**但仅 (role,api) 被消费**(load_role_permissions auth.py:65;require_perm/:auth/me/管理页三消费点)
- 管理页 Permissions.vue=13 api 键平铺(GET/POST /api/permissions 全量重写 role allow 集)
- 玻璃盒=骨架(myperms 弹窗列 permission tag,无来源/无 override 维度)
- 菜单=me.permissions(api 键)驱动(15号批四)

## 产出 1:后端三维解析链+override CRUD

- **文件**:`src/web_api/auth.py`+`src/web_api/routes/auth_routes.py`
- `load_role_permissions()` → `load_effective_permissions(username, role)`:**user deny > user allow > role allow > 默认拒**(10 号 §3 合并序);60s 缓存键带 username
- `require_perm`/`/auth/me` 换调 effective 版(me.permissions 含 override 合并结果)
- **GET /api/permissions 扩展**(admin):返回 `{api:{keys,roles:{}}, nav:{items,roles:{}}, data:{fields,roles:{}}, user_overrides:[{username,dimension,resource,effect}...]}`——nav items=菜单 v2.1 四组 16 项清单(与 MainLayout 菜单同源常量);data fields=市场域(astock/convertible/etf/crypto)+敏感级(detail/aggregated/count)
- **POST /api/permissions/user/{username}**(admin):body {dimension, resource, effect ∈ allow|deny|clear}——clear=删该行;写入带 updated_by 审计;invalidate 缓存(含该 user 键)
- **POST /api/permissions/{role}** 扩 dimension 参数(缺省 api 兼容现前端);nav 三态=resource 存 hidden|readonly|readwrite(单值行,effect 恒 allow);data 同构
- 系统安全策略锁(resume-admin/halt 全员)**注释明示不在表内管理**(维持现锁定语义)

## 产出 2:Permissions.vue 三维矩阵重写(10 号 §4 原型)

- 顶部:`[角色 ▾][用户 ▾]` 双选择器——选角色=编辑角色基线;选用户=override 编辑(只显 override 行+clear 按钮+新增 override)
- 三个 tab:**① 菜单/页面** 16 项 × 三态点选(hidden/readonly/readwrite);**② API** 13 键 × 读/写开关(api 键现语义=开关;系统策略键标 🔒 不可关);**③ 数据** 市场域勾选+敏感级三档
- 顶部常驻提示条:"后端强制 · 菜单只是显性化 · 变更全程审计"
- 保留现 13 键 API 管理能力(tab ② 即现功能迁移,不丢)

## 产出 3:玻璃盒增强(10 号 §4 配套)

- `/auth/me` 增 `perm_sources`:每 permission 标来源(role-base/user-override)+user deny 行显式列出("被拒:xxx(用户规则)")
- MainLayout myperms 弹窗:分组渲染(角色基线/个人覆盖/被拒项)+规则来源列

## 不做的(W5+)
- data 维度**执行面**(持仓聚合/risk_log 计数脱敏——动业务端点,与 D 阶段同批)
- 账户域细粒度/市场域 per-user 分配执行面(D 阶段)
- nav 三态的**路由守卫消费**(readonly 态拦截写操作——W5;本批 readonly 只影响 UI 呈现)

## 验收
1. pytest 全绿(+effective 解析序测试:user deny 压 role allow/user allow 补 role 缺/nav 三态 CRUD/override clear/空集防线)
2. 本地:管理员建 override(deny trader 用户某键)→该用户 /auth/me permissions 不含该键+perm_sources 标注;撤 override 恢复
3. Permissions.vue 三 tab 可编辑可保存(角色基线+用户 override 双路径);现 13 键功能不回归
4. 玻璃盒:分组+来源可见;viewer/analyst 自查无异常
5. smoke+build 绿

## 风险
- 缓存键变化(带 username):旧 60s 缓存失效逻辑+invalidate 全量清——回归面=require_perm 全部调用点(测:改权限后 60s 内旧值窗口,现行为一致)
- nav 16 项清单前后端同源:后端常量定义,前端从 GET 拿(不硬编码第二份)
- 用户 override 误锁 admin:系统策略键(resume/user_mgmt 等)override 拒绝编辑(UI 锁+后端校验双防线)
