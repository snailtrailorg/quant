# 批 136 · 安全依赖升级（Pillow 双档 / aiohttp 3.14.5）+ 头像解码闸门前移 · 已收官

> 立项 2026-10-21（安全态势页 AI 摘要触发：当日 15 条漏洞，pillow 8 / aiohttp 3）。
> **2026-10-22 上产** `202610221540-7ce71ab`，staging + prod 双绿。

## 环境事实（一手证据，本批最关键的发现）

| 环境 | Python | 证据 |
|---|---|---|
| prod | **3.11.13** | 运行态 `/api/security/snapshot` 的 `components.python` |
| staging | **3.10** | release 阶段 5 pip 预检拒绝 `pillow==13.0.0`（`Requires-Python >=3.11`） |
| dev | 3.10 | `server/venv` |

⇒ **部署环境版本漂移**：同一份 lock 必须同时满足 3.10 与 3.11。

## 产出

1. **`requirements.txt`**：Pillow 双分支（`>=13.0.0; python_version>="3.11"` / `>=12.3.0; python_version<"3.11"`）+ `aiohttp>=3.14.4`。
2. **`requirements.lock`**（部署按它装，`--require-hashes`——**只改 txt 会假绿**）：
   - `pillow==13.0.0 ; python_version >= "3.11"`（cp311 轮子 hash）
   - `pillow==12.3.0 ; python_version < "3.11"`（cp310 轮子 hash）
   - `aiohttp==3.14.5`（cp310 + cp311 轮子 hash **并集**）
   - 其余包零漂移（multidict 保持 6.9.1——实测 aiohttp 3.14.5 接受）。
3. **头像解码闸门前移**（`auth_routes.py`）：格式白名单校验从 `load()` 之后移到之前（`format` 由 `open` 解析 header 即得，无需先完整解码）——收紧 Pillow 解码类 CVE 的攻击面。
4. **版本满足闸门**（`tests/test_requirements_lock_consistency.py`）：原闸门只比**包名**，查不出「lock 仍钉旧版」；新增按**两个部署目标（3.10 / 3.11）分别校验**、支持同名多 pin 带 marker。

## 验收证据

- 干净 py3.10 venv dry-run ⇒ `pillow-12.3.0 + aiohttp-3.14.5`
- 干净 py3.11 venv dry-run ⇒ `pillow-13.0.0 + aiohttp-3.14.5`
- 上产后 `quant-pip-wrapper dry-run` 直读 venv：prod `pillow==13.0.0 / aiohttp==3.14.5（python3.11）`；staging `pillow==12.3.0 / aiohttp==3.14.5（python3.10）`
- 版本闸门 19 例绿，判别力实测（txt>=13 vs lock 12.3 在 3.11 目标下必红）；全量 pytest 绿

## 挂账

- **staging/prod Python 漂移整治**：彩排装不了 prod 的 Pillow 13，且任何「要求 ≥3.11」的依赖都会在彩排阶段假红 ⇒ staging 升 3.11 或统一镜像。