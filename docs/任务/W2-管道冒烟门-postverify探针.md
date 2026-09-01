# W2 管道冒烟门:postverify 端点断言(P1-2,2026-09-01 拉前;完美系统战役第二批)

> 排期:plan.md 战役表(原 09-03,用户裁定推进)。目标:部署后管道自动断言核心 API 行为
> (今天 backtest 500 类运行时错误,healthz/readyz 拦不住——进程活≠功能对)。

## W6 制度符合性声明
- **免账号/免密钥**:不建探针账号(shared/.env quant:600 deploy 不可读,密钥双存违 secrets 隔离——已弃)
- 暴露面=**/readyz 同待遇**:nginx 内网 allowlist 403(现有范式 quant.conf:44)+ 端点内卫(X-Real-IP 非内网→403,防 nginx 规则未装窗口)
- 只读诊断,零权限变更——符合三权分立/最小权限

## 产出 1:探针端点 `GET /api/_probe`
- **文件**:`server/src/web_api/routes/system.py`(healthz/readyz 同居)
- **检查集**(全只读,单请求聚合):db(SELECT 1)/factors 注册表(list_factors 计数)/strategy_config 计数/notifications 计数/valkey ping/hub 心跳在场(quant:hb:md-hub TTL)
- **返回**:`{"ok": bool, "checks": {db: "ok"|"fail: ...", ...}}`(200 恒返,看 ok 字段——管道断言 json.ok)
- **内卫**:X-Real-IP 存在且非私网(RFC1918/loopback)→403(nginx 代理流带此头=外网;管道 localhost 直连无此头)
- 依赖故障不 500:单项 fail 记入 checks,ok=false

## 产出 2:nginx 外封
- **文件**:`server/scripts/nginx/quant.conf`——`location = /api/_probe` 复制 /readyz 的 allowlist 块
- **装位**:nginx conf 为一次性手工装(管道不自动)——给用户命令(五要素),装前内卫已兜

## 产出 3:release.yml 阶段 8 探针任务
- readyz 任务后追加:开关 `deploy_postverify_probe | bool`(staging/prod=true,sandbox=false——沙箱 web 桩无此端点,同 postverify_healthz_release 豁免范式)
- `uri localhost:<web 波端口>/api/_probe`(无代理头)→ assert 200 且 json.ok==true;失败走既有 rescue=回滚
- rollback.yml **不动**(回滚后旧代码可能无探针,healthz 足够)

## 验收
1. pytest 全绿(+探针单测:全好 ok=true/单项坏 ok=false 且 200/外网头 403)
2. 沙箱场景套件 run_scenarios.sh 全绿(沙箱豁免路径)
3. staging 彩排=真验证(阶段 8 探针任务首跑,绿=端点+管道+断言三通)
4. 本地 dev:curl /api/_probe 无头 200 ok;带假公网 X-Real-IP→403

## 风险
- 探针任务误红回滚:检查集全只读低风险,但 notifications 计数等若未来加严可能误伤——检查集保守起版(六项)
- 端点性能:六检查 <1s,DB 故障时 db 检查挂 timeout?各检查 try/except 快速失败(SELECT 1 无 timeout 风险小)
