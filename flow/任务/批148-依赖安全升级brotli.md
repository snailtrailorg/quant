# 批 148 · 依赖安全升级 brotli 1.2.1（GHSA-9v8h-qqgm-4m35 Critical）

> 立项 2026-10-25（用户通报新 CVE 清单）。安全批走既有通道（先例批 139 certifi 盘中 force 直上）。
> 关联：批 139（紧急安全升级先例）· grype 安全扫描面

## 目标

brotli 1.2.0→1.2.1（越界写 Critical）；filelock 同批评估处置（见现状 3）。

## 现状（已实证 2026-10-25）

| # | 事实 | 锚点 |
|---|---|---|
| 1 | brotli 实装 1.2.0；GHSA-9v8h-qqgm-4m35（解码器越界写）1.2.1 修复；pypi 1.2.1 可用 | pip index；pip show |
| 2 | 两包均**传递依赖**非直接：brotli←httpx/celery/urllib3/fonttools/aiohttp；src 零直接 import | /tmp/rdeps.py 实查；grep 零命中 |
| 3 | filelock 4.1.0：CVE-2026-1129xx 系列修复版 **4.2.1 未在 pypi 发布**（最高 4.1.0）；攻击面=ReadWriteLock/SoftFileLock/Windows——本仓使用方 CacheControl/setuptools，无 ReadWriteLock 使用，Linux 无 Windows 面，私有单机部署 ⇒ 实际暴露≈0，**等 4.2.1 发布随下批**（挂账带触发条件） | pip index versions filelock |
| 4 | requirements.txt 无两包 pin（NONE）——升级需追加下限 pin 防回漂 | grep 实查 |

## 依赖（就绪）

无。

## 引用

- 批 139 任务档案（安全升级通道先例：staging 冒烟→prod，非交易时段或 force）

## 产出

1. `server/requirements.txt` 追加 `brotli>=1.2.1`（安全下限 pin）。
2. venv 实升（本地+部署管道冻结面自动带）。
3. 冒烟：httpx/celery/aiohttp import 三连 + healthz。

## 限定范围

只动 requirements.txt 一行 + 升级动作。**不碰**：filelock（等 4.2.1）、其他依赖。

## 接口契约

无代码接口。依赖变更：brotli 1.2.0→1.2.1（C 扩展重编）。

## 验收标准

1. `venv/bin/pip show brotli` → 1.2.1
2. `venv/bin/python -c "import httpx, celery, aiohttp"` → OK
3. 全量 pytest 绿
4. 上产后 healthz/readyz 绿 + 心跳三证

## mock 方式

无——依赖升级即验证载体。

## 回滚

`pip install brotli==1.2.0` + revert requirements 行（无 schema 无数据面）。
