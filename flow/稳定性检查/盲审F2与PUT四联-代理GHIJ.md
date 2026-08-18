# 盲审 · F2 根因 + PUT 改造 四联（G/H/I/J，2026-08-18，八段工作线首批）

> 首批按用户定型的「方案→审核→代码→审核→本地测试→部署→生产测试→提交」执行：G 审 F2 设计、H 审 F2 代码、I 审 PUT 设计、J 审 PUT 代码。全部发现实证复核后修复。

## G（F2 设计审）：有条件批准 → 4 条全落
- S1 三态必须只作用于返回 last_success_date 键的 handler——否则分钟线（per-symbol 失败粒度）200 积分全失败冻游标 → beat 每 30min 重试风暴
- S2 _mark_running(False) 无条件覆盖 idle——终态必须后写
- S3 全失败仍刷 last_sync_ts（旧 ts 会让调度器连发重试）
- S4 空 df 记成功（节假日 freq=B 空数据，记失败=游标永久卡节前）
- 顺手发现：DataManage row.status 键名错位（API 返回 last_status——状态列从未显示过）

## H（F2 代码审）：需修后部署 → 两严重已修
- H-S1 无 handler 路径 r 未定义 NameError（新引入回归，sync_log 双记）——原代码还有 0/0 假 success 推游标；改显式 error 返回
- H-S2 NULL 游标全失败 fallback 写窗口起点=下轮永久跳过起点日（与 F2 同构的 off-by-one）——改起点前一日
- 盲区补测 3 例（无 handler/回补不动游标/缺列 continue）；告警口径统一用终态

## I（PUT 设计审）：补充后批准
- **阻断级：路由遮蔽**——{sid}/{bid}/{name} 转 POST 后会吃掉后注册的静态路由（validate-python 等 4 端点 422 静默错乱）；修法=静态路由注册在前
- 清单勘误：前端 17（非 12+17）；文档 web_api.md 实为 14 处+08-web-admin.md 6 处（我方 grep 只匹配行首形态漏了复合写法）
- 验证三连（openapi/负向 405/静态可达）；index.html no-cache 防旧 bundle 打已删动词

## J（PUT 代码审）：需修后部署 → 测试重写
- **变异测试实证**：test3（401-即-可达）恒绿死测试，4 静态端点只护住 1——重写为结构化路由顺序断言（全路由覆盖，回退/重排即红）
- 产品代码四项全合格（调序字节级一致/150 路由运行时扫描无遮蔽/前端零残留/nginx 内部重定向语义正确）
- 建议分两笔提交（已照办）；分节注释归位

## 生产验证段收获
- PUT→405 / POST 200 / validate-python 真 valid / sync/config 写路径 200
- **顺带修既有 bug**：/api/sync/log 自出生 500（SELECT 列名 start/end 与写入 schema start_date/end_date 从未对上+保留字）——生产验证的额外价值

（测试 294 绿；两笔提交 d4b6409/21ae476）
