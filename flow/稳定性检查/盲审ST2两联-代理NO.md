# 盲审 · ST2 两联（N/O，2026-08-18）

## N（设计审两轮）：v1 不通过 → v2 条件批准
- **N-F1 空批不可表示**（清仓 0 行回报→max(ts) 停旧批→页面永久显示已清仓持仓）：改当前状态表（同事务 DELETE+INSERT）
- **N-F2 EVENT_POSITION handler 架构性错误**（direct 模式 vnpy init_query 每 4s 常推→900 行/时+散批）：改 60s 循环取 query_position() 返回值
- N-S3/S4 direction+account_id 列；N-S5 stale≠空仓（position_refresh 心跳）；N-S6 幽灵缓存清除；量级由 DELETE+INSERT 天然消解

## O（代码审）：需修后部署——三致命全实锤全修
- **O-F1 conn.executemany 不存在**（F 审踩过的同款坑重蹈——MagicMock 掩盖 API 误用）：走 cursor + ON CONFLICT 幂等
- **O-F2 reconcile 符号命名空间断裂**（vt_symbol "600000.SSE" vs 裸 "600000"，join 永不命中→每小时误报）：两侧 split_part 归一
- **O-F3 hub 挂点在断线守卫外**（TD 断线→[] →写"新鲜空仓"假真相，恰是 N-S5 要防的）：镜像 direct 入 if accounts:
- O-S1 逐行推送部分批次：两拍稳定等待；O-S2 底仓/场外单天然差异=对账页展示不加告警码

## 生产首验（18:33）
18 标的券商真值 / stale=false / **direction=Net 实证不过滤的正确**（过滤 'long' 全漏）/ 第四比对符号归一命中报真差异

## 结构性教训
**MagicMock 全绿掩盖 API 误用**（O-F1：8 例测试挡不住 executemany 拼错）——真连接冒烟测试（本地 dev DB）已入套件；conn.executemany 坑两次出现，已进踩坑记录候选
