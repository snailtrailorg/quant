# 批 56b：M1 ts UTC aware 立法迁移（23/24 号变更兑现）

> 29 号 §四"ts UTC 迁移工程预案六条"=本批生死规范。**三前置缺一不开工**（29 号 §十三）。

## 目标
全部数据表 ts 列 naive→timestamptz(UTC aware)；db.py pin TimeZone=UTC；代码 as_shanghai→as_utc 全替换。

## 依赖（就绪）
- 批 56a ✅（类型钉+三表）
- **待办前置①**：staging 同规模实测（naive→timestamptz 全表重写 ALTER 计时，时长写回本文件）
- **待办前置②**：staging 演非交易日 deploy_trading_windows 行为（假期闸门预演）
- **待办前置③**：节前 10/6-10/7 端到端同步演练+回测冒烟

## 产出
迁移 0093（全表 ts 等值改写）/db.py 时区 pin/全仓 as_shanghai 替换/去重键 epoch 归一/暖机拼接+日界沿读侧回归两例（用例在本文件开工时定义）。

## 限定范围
只改 ts 表示层——不改任何数据值/同步逻辑（行为等价）。暖机/hub 聚合/日界沿三处 naive 构造排查修复。

## 接口契约
as_utc(dt)->datetime(UTC aware)（tz.py 新函数，签名同 as_shanghai）；去重键比较走 int(ts.timestamp())。

## 验收标准
①逐表聚合校验和迁移前后一致（count+sum(epoch(ts))）②暖机拼接/日界沿两例回归绿 ③现有测试全绿 ④PITR 基线已打（运维步骤入部署 runbook）⑤停服+beat 暂停+同版本原子上线三检查单过。

## mock 方式
校验和对照用真库（行为级）；tz 转换单测纯函数。

## 参考文档
29 号 §四（工程预案六条+风险诚实声明）；28 号 §3.2（tz 立法+冲突标注）。
