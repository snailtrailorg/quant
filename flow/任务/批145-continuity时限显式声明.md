# 批 145 · data_continuity_check 时限显式声明（消软限噪音）

> 立项 2026-10-24（用户裁定立项观察窗预核发现 1/2）。来源＝10-24 观察窗预核（`flow/稳定性检查/观察窗判据汇总.md` 发现 2）。
> 关联：批 110·D（时限显式声明先例——`server/src/scheduler/tasks.py:1734` 起 docstring）· P-MON-006（任务本尊）

## 目标

data_continuity_check 补专属 soft/time limit，消除每轮 ~28min 慢轮触发全局 300s 软限的 SoftLimitExceeded 噪音告警（4 天 29 次，无熔断无失败但污染 journal 判读）。

## 现状（已实证 · prod，2026-10-24 预核）

| # | 事实 | 锚点 |
|---|---|---|
| 1 | 任务装饰器无专属时限 → 吃全局 `task_soft_time_limit=300` | `server/src/scheduler/tasks.py:551-552`；全局 `app.py:132` |
| 2 | 慢轮耗时 1664~2093s（修复动作重），每轮 :17 起跑 :44-:51 落成；SoftLimitExceeded 告警后**继续跑完不熔断**——机制=软限在补采段被 `except Exception` 吞除（SoftLimitExceeded 继承 Exception，tasks.py:635 一带）→ 任务带半程数据到完成态返回 ok | prod journal 10-22/10-23 双峰分布（预核报告发现 2）；机制措辞=盲审 A-P1/B-P2 同点修正 |
| 3 | 快轮（无修复动作）24~63s；慢轮仅交易日盘后出现 | journal 耗时归并表 |
| 4 | 批 110·D 已为 reconcile 类立显式声明先例（初值 1800/1980 + 首个实跑轮校准机制） | `tasks.py:1734-1746` docstring |

## 依赖（就绪）

无前置；独立小批。

## 引用（设计 / 方案）

- `flow/稳定性检查/观察窗判据汇总.md` §预核发现 2 —— 耗时分布实证
- `server/src/scheduler/tasks.py:1734` 批 110·D docstring —— 显式声明同制先例（软限直抛语义/校准机制）

## 产出

1. tasks.py :551 装饰器改 `bind=True, soft_time_limit=2400, time_limit=2700`（慢轮实测峰 2093s + ~15% 余量；对齐仓内重任务 4200/2400 档位序列中的 40min 档）；**签名同步改 `def data_continuity_check(self):`**（bind=True 须收 self——仓内 9 处 bind 先例全收，无参先例 security_scan(self)；盲审 B-P0 修正）。
2. docstring 补两行：实测依据（1664~2093s）+ 校准规则（若实跑峰 >2400s 改常量重跑门，沿批 110·D 制）。

## 限定范围

只改 tasks.py 一处装饰器 + docstring。**不碰**：任务逻辑、beat 周期、全局 task_soft_time_limit、其他任务时限。

## 接口契约

- 装饰器加 bind=True + 签名改 `data_continuity_check(self) -> dict`（调用面零变化——beat 按名触发，无代码直调）。
- 回滚：git revert（纯代码小批，无迁移无 DB 副作用）。

## 验收标准

1. `cd server && venv/bin/python -m pytest tests/ -q` → 全绿
2. `grep -n 'data_continuity_check' -B2 server/src/scheduler/tasks.py | grep soft_time_limit` → 命中 2400
3. 上产后首个慢轮 journal 无 SoftLimitExceeded（该任务）——观察窗内顺带核验

## mock 方式

无新测试（纯装饰器参数）；既有 continuity 相关测试全量回归即可。
