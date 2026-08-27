# 本地开发部署

> 一键脚本：`bash scripts/dev-start.sh [start|stop|restart|status|logs]`
> 本文件说明脚本做的事 + 排错 + 一次性前置准备。

## 快速开始

```bash
# 启动（含 alembic 迁移 + 后端 :8000 + 前端 :5173）
bash scripts/dev-start.sh start

# 查状态
bash scripts/dev-start.sh status

# 停 / 重启 / 看日志
bash scripts/dev-start.sh stop
bash scripts/dev-start.sh restart
bash scripts/dev-start.sh logs       # tail -f 后端日志
```

启动后：
- 后端 http://127.0.0.1:8000（health: `/health`）
- 前端 http://127.0.0.1:5173
- 默认账号 `admin / admin123`

## 脚本做了什么

`scripts/dev-start.sh start` 顺序执行：

1. **前置检查**（失败给出修复命令）：
   - PG（`pg_isready -U quant -d quant`）— 没起 → `sudo bash scripts/dev-init-db.sh`
   - Valkey（`valkey-cli ping`）— 没起 → `bash scripts/dev-init-valkey.sh`
   - venv（`server/venv/bin/python`）— 没建 → `cd server && python3.10 -m venv venv && ./venv/bin/pip install -r requirements.txt`
   - node_modules（`web/node_modules`）— 没装 → `cd web && npm install`
2. **跑迁移**：`cd server && alembic upgrade head`
3. **启后端**：`setsid ./venv/bin/uvicorn src.web_api.main:app --port 8000` → 日志 `/tmp/quant-uvicorn.log`，轮询 `/health` 最多 15s
4. **启前端**：`cd web && setsid npx vite --port 5173 --host 127.0.0.1` → 日志 `/tmp/quant-vite.log`，轮询首页最多 15s

幂等：已在运行的服务跳过，不重复启。

## 一次性前置准备（新机器/新克隆才做）

```bash
# 1. PG：建 quant 角色 + 库 + pgvector（需 sudo 改 pg_hba）
sudo bash scripts/dev-init-db.sh

# 2. Valkey
bash scripts/dev-init-valkey.sh

# 3. 后端依赖（venv 用 Python 3.10，不用 3.14，详见 CLAUDE.md 技术栈约束）
cd server
python3.10 -m venv venv
./venv/bin/pip install -r requirements.txt

# 4. 前端依赖
cd ../web
npm install

# 5. .env（从 .env.example 复制后填 Tushare token 等）
cp server/.env.example server/.env
```

## 排错

| 现象 | 原因 / 解决 |
|---|---|
| `PG: ✗ 未启动` | `sudo bash scripts/dev-init-db.sh` 建角色/库 |
| `Valkey: ✗ 未启动` | `bash scripts/dev-init-valkey.sh` |
| `venv: ✗ 不存在` | `cd server && python3.10 -m venv venv && ./venv/bin/pip install -r requirements.txt` |
| `node_modules: ✗` | `cd web && npm install` |
| 后端启动失败 | `bash scripts/dev-start.sh logs` 看日志；常见是端口占用（`lsof -i :8000`）或迁移报错 |
| 前端启动失败 | `tail -20 /tmp/quant-vite.log`；常见是端口占用或依赖缺失 |
| alembic 迁移报错 | `cd server && ./venv/bin/alembic history` 看链；`alembic upgrade head` 手动跑看详细错 |
| 改了前端代码不生效 | Vite HMR 自动热更；没生效看 `/tmp/quant-vite.log` 是否编译错 |
| 改了后端代码不生效 | uvicorn **没加** `--reload`，需 `bash scripts/dev-start.sh restart` |

## 端口占用排查

```bash
lsof -i :8000    # 后端
lsof -i :5173    # 前端
# 杀残留：
pkill -f "uvicorn src.web_api.main"
pkill -f "vite --port 5173"
```

## 全链路验证

启动后跑验证脚本（测各模块导入 + DB + 风控 + 前后端 health）：

```bash
bash scripts/verify.sh
```

## 不在本脚本范围

- **实盘 XTP 连接**：需 `broker_config` DB 配 XTP 凭证或 `.env XTP_TEST_*`，见 `docs/architecture/模块契约/strategy_framework.md`
- **飞书**：需 `feishu_config` DB 配凭证 + 扫码，见 `docs/architecture/11-feishu-lark.md`
- **生产部署**：`deploy/DEPLOY.md`（Ansible 三命令+彩排先行+回滚）