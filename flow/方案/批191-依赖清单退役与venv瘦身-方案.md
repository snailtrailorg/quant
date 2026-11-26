# 批191 · 依赖清单退役与 venv 瘦身（方案）

> 状态：待确认。涉及部署机制（requirements-dev.txt 退役 + 新 wrapper 子命令），按 A06 走；wrapper 装位由用户执行。

## 1. 环境机制（先对齐）

| 环境 | 依赖安装来源 | 是否走 deploy |
|---|---|---|
| dev | `server/venv`，仅 `pip install -r requirements.txt`（dev-start.sh 提示） | 否 |
| staging | `shared/venv`，deploy 管道（lock + requirements-dev.txt） | 是 |
| prod | 同 staging（同一套 all.yml/wrapper/白名单） | 是 |

- dev 与 staging 同一物理机，但依赖目录、venv、部署链互相独立。
- **`requirements-dev.txt` 的真实消费者是 staging/prod，不是 dev**；dev 从不读它。命名是批 120 遗留错位。

## 2. 现状事实（实测）

- `requirements-dev.txt` 内容 4 行：`ruff`、`pybind11`、`importlib_metadata`、`filelock`。
- 三包在 `requirements.lock` 中**均不存在**（lock 只管 requirements.txt 闭包）。
- prod 实装：ruff 0.17.5、pip-tools 7.6.2、akshare 1.20.65、pybind11 3.1.0、importlib-metadata 10.1.0、filelock 4.2.10。
- 代码引用：仅 `packaging` 被 import（已补 requirements.txt）；ruff/pip-tools/akshare 零引用。
- 部署链引用点（全 6 处）：
  - `deploy/inventory/group_vars/all.yml:15`（deploy_slices）、`:36`（sync whitelist）
  - `deploy/playbooks/release.yml:369-371,388`（指纹判据）
  - `deploy/wrappers/quant-pip-wrapper:103-105,126-128`（install/rebuild 的 DEV 段）
  - `deploy/tests/make_sandbox_root.sh:65`（沙箱占位复制）
- sudoers 只列 wrapper 路径、不限参数 ⇒ 新子命令无需改 sudoers，但仍需重装 wrapper 内容（用户装位）。

## 3. 设计决策

### 3.1 ruff：dev/staging 用 OS 包，prod 不装

- dev/staging 同物理机（Fedora）：`dnf install ruff`（仓库 0.17.4）；系统级安装，dev 与 staging 两个逻辑环境都可用。
- prod（Alibaba Cloud Linux 3）：仓库无 ruff 包，且生产零引用 ⇒ 不装，也不要为此引入 EPEL/COPR。
- staging 的 SCA 会扫到一条 **OS 域** ruff，而 prod 没有——这是**预期差异**：两台机器操作系统不同，系统包本就不可能 100% 一致；**部署配置（依赖清单/wrapper/sudoers/单元/白名单）一致才是判据**。
- 因此 ruff 不再出现在 PyPI 域台账/可更新计数中（从 pip 侧退役成功），OS 域差异不追平。

### 3.2 requirements-dev.txt 退役

不是整文件删除，而是把生产真需要的三包迁入 `requirements.txt`（进 lock），再删文件：

| 包 | 去向 | 理由 |
|---|---|---|
| importlib-metadata>=8 | requirements.txt | vnpy_xtp（lock 外 wheel）运行时依赖 |
| pybind11>=2.12 | requirements.txt | quant-emd-build 编译 EMQ/EMT 绑定 |
| filelock>=4.2.1 | requirements.txt | pip/setuptools/wheel 工具链伴生（升级链） |
| ruff>=0.9 | 删除 | 纯开发工具；dev/staging 由 OS 包提供 |

- 三包进 lock 后版本由 pip-compile 解析（importlib-metadata 可能 10.1.0→10.2.0，API 稳定）。
- **同时修 `scripts/lock-sync.sh`**：补 `--no-annotate`。本轮实测缺它会导致 lock 全文件格式漂移（270 行噪声、注释形态变化），掩盖真实变更。

### 3.3 三包从 staging/prod venv 卸载

- 目标：ruff、pip-tools、akshare。
- 通道：`quant-pip-wrapper` 新增 `prune` 子命令，**硬编码**上述三包（不接受任意包名，防注入面）：
  ```
  quant-pip-wrapper prune    # 幂等：pip uninstall -y ruff pip-tools akshare
  ```
- 参数校验：`prune` 不收参数；case 后统一 release_id 校验需对 prune 豁免（照 rebuild 先例）。
- 不改 sudoers（只列 wrapper 路径）；wrapper 属 root，需重跑 bootstrap 装位（用户执行）。
- 卸载不动依赖（pip uninstall 只删目标包），不影响共用包。

## 4. 变更清单

1. `server/requirements.txt`：+3 行（importlib-metadata / pybind11 / filelock）。
2. `server/requirements-dev.txt`：删除（ruff 行不迁）。
3. `server/requirements.lock`：重编（lock-sync 修 --no-annotate 后）。
4. `scripts/lock-sync.sh`：补 `--no-annotate`。
5. `deploy/inventory/group_vars/all.yml`：deploy_slices 与 sync_whitelist 删 requirements-dev.txt。
6. `deploy/playbooks/release.yml`：删除两处 requirements-dev 指纹判据。
7. `deploy/wrappers/quant-pip-wrapper`：删两处 DEV 安装段；新增 `prune` 子命令与白名单常量。
8. `deploy/tests/make_sandbox_root.sh`：删 requirements-dev 占位复制。
9. 测试：wrapper prune 白名单/幂等（shell 断言）；lock-sync 输出稳定性；`grep -rn requirements-dev` 全仓 0 命中。

## 5. 迁移顺序（强制）

1. 代码提交（1-9），全量测试绿。
2. 用户装位：staging + prod 重跑 bootstrap（新 wrapper 生效）。
3. staging 发布（验证：lock 含三包、requirements-dev 缺失不报错、release 绿）。
4. staging 执行 `quant-pip-wrapper prune`；`pip check`；重扫安全页确认 pip 可更新 16→13。
5. prod 发布 → prune → 同样验收。
6. dev/staging 装 ruff：`dnf install ruff`；`ruff --version` 验证（prod 不装）。

## 6. 验收证据

- staging/prod：`quant-pip-wrapper freeze | grep -E '^(ruff|pip-tools|akshare)=='` ⇒ 空。
- `pip check` ⇒ No broken requirements。
- `/api/security/snapshot` 的 pip actionable = 13（原 16），列表不再出现三包。
- `grep -rn requirements-dev server/ deploy/ scripts/` ⇒ 0 命中。
- prod readyz/心跳正常；release 指纹变化触发一次真装且装后仍无三包。
- dev/staging：`ruff --version` 可用（OS 包）；prod：无 ruff，且 pip 域台账无三项。

## 7. 风险与回滚

- importlib-metadata 升级至 10.2.0：vnpy_xtp 仅用标准 API，风险低；失败可 pin 回 10.1.0 重编。
- pybind11 版本随 lock：只影响 EMQ 编译；若下降级需重编 .so（quant-emd-build 幂等）。
- 卸载误伤：目标三包零引用、无 Required-by；`pip check` 兜底。
- 回滚：`pip install ruff==0.17.5 pip-tools==7.6.2 akshare==1.20.65 -i <index>`（一次性）；清单回滚走 git revert + 重编 lock。
- 旧 release 目录仍含 requirements-dev.txt：新 wrapper 已删该段；旧 wrapper 在装位前仍有 `-f` 保护，不会因文件缺失报错。

## 8. 待确认

1. `prune` 固定“一次性”还是每次 install 后自动跑？三包已不在任何清单，复发面=0，建议一次性。
2. ruff 的 OS 包版本（0.17.4）与 venv 原版本（0.17.5）差一个小版本，lint 规则可能略有差异；dev 侧接受否。
