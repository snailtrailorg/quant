# W1 快赢批:DSL 因子试算 + runbookOf 微修(2026-09-01 提前开工,完美系统战役第一批)

> 排期:plan.md 战役表(原定 09-02,用户裁定提前今日)。本地部署测试(dev-start 环境+smoke 门)。

## 产出 1:DSL 因子试算(#5)

- **后端** `server/src/web_api/routes/strategy.py` `preview_factor_api`(~L249):body 增 `type`(缺省 python,docstring 字段表同步——盲审 A-P2);`type=dsl` → `DSLFactor("preview", code)`(构造期即校验,ValueError→现有 error 返回路径)——喂 bar 循环/BarContext/stats 零改动(A 核对:别名坑不踩,history 键 'open' 与 _series 归一全对齐)
- **前端** `web/src/views/Factors.vue`(盲审 A-P1/P2 修入):①**两个**试算提交体都要带 type——对话框 `previewFactor`(~L228 `type: form.ftype`)+表格行 `previewFactorFor`(~L340 `type: row.type`,DSL 行取 `row.params.expr` 作 code——registry entry 无 code 字段);②DSL 分支(L102-109)是**结构性无按钮**非 disabled——实现=DSL 块内加试算按钮(走 previewFactor);python 块原样

## 产出 2:runbookOf 双调用消(#8)

- `web/src/layouts/MainLayout.vue`:loadNotifs 时预计算 `rb: runbookOf(n.code)`,模板 **4 处**(L87/88/90——chip v-if+label、guide 行 v-if+插值,盲审 A-P2)全换 `n.rb.*`;旧通知 code=null→rb=null→v-if 不渲染(无异常路径)

## 产出 3:#7 顺手打码——本批如实声明

- 本批触碰文件(strategy.py 路由/factor.py?不动/Factors.vue/MainLayout.vue)经 grep **无 notify 调用点**——顺手原则无对象,不硬造;W3(09-04)扫荡批统一清

## 验收
1. pytest 全绿(preview dsl 分支加 1 测:好表达式出值/坏表达式 error 返回非 500)
2. 本地 API 实测:POST /api/factors/preview {type:dsl, code:"mean(close,20)/close-1"} → values 非空+stats.last 数值
3. smoke-web.sh 20/20(本地部署门)
4. web build 绿

## 风险
- 无迁移/无 schema 改动/无生产服务变更——纯只读端点扩展+前端渲染微调,风险面极小
