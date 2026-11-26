# 批191 · 依赖清单退役与 venv 瘦身（实施终态）

> 已上产：staging `202611261345-6611f70`（同日 staging `202611261133-d0d4007` 起）、prod `202611261345-6611f70`。
> 机制变更（wrapper 内容 + 部署输入清单），wrapper 已由 michael 重跑 bootstrap 装位。

## 1. 环境机制

| 环境 | 依赖来源 | 走 deploy |
|---|---|---|
| dev | `server/venv`，仅 `requirements.txt` | 否 |
| staging | `shared/venv`，lock + （原）requirements-dev.txt | 是 |
| prod | 同 staging（同一套 all.yml/wrapper/白名单） | 是 |

dev 与 staging 同物理机、逻辑独立。`requirements-dev.txt` 的真实消费者一直是 staging/prod，dev 从不读它——命名错位，本批退役。

## 2. 依赖清单退役

- 生产真需要的三包迁入 `requirements.txt`（进 lock）：
  - `pybind11>=2.12`（EMQ/EMT 绑定编译）
  - `filelock>=4.2.1`（pip/setuptools/wheel 工具链伴生）
  - `importlib-metadata>=8`（vnpy_xtp 运行时依赖）
- `ruff>=0.9` 从清单移除：dev/staging 由 OS 包提供（Fedora `dnf install ruff` 0.17.4）；prod（Alibaba Cloud Linux 3）无该包且生产零引用，不装。
- `server/requirements-dev.txt` 删除；deploy 链 4 处引用清理（`all.yml` slices/whitelist、`release.yml` 指纹判据、`quant-pip-wrapper` 两处 DEV 段、`make_sandbox_root.sh`）。
- `dep_audit` 不再解析 dev 名单（组别统一 runtime）。
- `lock-sync.sh` 补 `--no-annotate`：缺它会因注释/命令行使 lock 全文件漂移（实测 270 行噪声）。

## 3. venv 瘦身（一次性 prune）

- `quant-pip-wrapper` 新增 `prune` 子命令：硬编码 `ruff pip-tools akshare`，不接受包名参数（零注入面），幂等。
- 子命令白名单 `freeze|upgrade-tools|dry-run|install|rebuild|prune`；`prune` 豁免 release_id 校验。
- staging/prod 各执行一次（`sudo -n -u quant /usr/local/sbin/quant-pip-wrapper prune`），三包已从 venv 消失。
- 不自动随发布跑：三包已不在任何清单，复发面=0。

## 4. 台账 GC：三域本轮未见即删

用户裁定：相信采集准确性，pip/os/binary 三域统一「本轮未见即删」，撤 3 天宽限。

**判据必须用本轮名单，不能比时间**：

```sql
DELETE FROM security_component
WHERE source='pip'   AND NOT (name = ANY(%s::text[]));   -- 名单来自本轮 _ledger
DELETE ... source='binary' AND NOT (name = ANY(...));
DELETE ... source='os'     AND NOT (name = ANY(...));    -- 名单来自本轮 _os_all
```

- 域级守卫：该域本轮无产出（采集降级）时跳过该域 GC，防一次失败清空台账；
  pip/binary 由 `_ledger` 非空守、os 由 `_os_all` 非空守。
- **为什么不能比时间**：`last_seen` 是 `date`（值为 00:00）。`last_seen < today` 在同日多轮漏判；`last_seen < now()`/固定锚点会把本轮刚写的行（00:00）全部判成“未见”而**清空全表**（2026-11-26 staging 实证，已重建）。
- 名单与 upsert 同源（`_merge_ledger`/`_os_all`），无口径漂移。

## 5. 验收（实证）

- staging：台账 `pip 161`、`os 3491`；三包 pip 行消失，探测行被 GC，仅剩 `os|ruff`（dnf）。
- prod：`releases/202611261345-6611f70`；三证过；`pip_total 177 / pip_yes 11`（原 14，-3）；`retired_present []`；`actionable 13`。
- 全量 **3527 passed**；前端/发布均绿。
- 三包卸载证据：`quant-pip-wrapper freeze` 无 `ruff/pip-tools/akshare`；新三包版本 `filelock 4.3.1 / importlib_metadata 10.2.0 / pybind11 3.1.0`。

## 6. 边界与遗留

- OS/binary 域在两台机器的差异属预期（OS 包不必一致；部署配置一致才是判据）。
- prod 本次为交易时段 `force=true` 发布（用户明确指令），留痕在 release 日志。
- dep_audit 判定列需下一轮 04:00 刷新后与 `has_update` 口径完全同步。
