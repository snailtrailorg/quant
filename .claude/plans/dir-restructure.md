# 目录重构：server/ + web/ 结构对齐 safebox

## 背景
当前 `scripts/` 混用：既放服务器需要的（init-seed.sql、systemd/），又放开发机专用的（deploy-*.sh、quant-deploy.sh）。`deploy-server.sh` 把整个 `scripts/` rsync 到服务器，导致开发机部署脚本被误传服务器（多余 + 含路径信息）。`src/` 也混了后端代码和前端 `web_ui/`。

参考 safebox 的 `server/` + `web/` 结构，分离后端/前端，部署时整体 rsync 对应目录。

## 新结构

```
Quantitative/
├── server/                    # 后端（本地开发 + 部署源，整体 rsync）
│   ├── src/                   # Python 代码（web_api/data_platform/...，import 路径不变）
│   ├── scripts/               # 服务器需要的脚本
│   │   ├── init-seed.sql
│   │   └── systemd/           # 3 个 .service 模板
│   ├── requirements.txt
│   ├── .env.example           # 模板（提交 git）
│   ├── venv/                  # 本地 venv（不提交，部署排除）
│   └── .env                   # 本地开发用（trust 免密，不提交，部署排除）
├── web/                       # 前端（= 现 src/web_ui/）
│   ├── src/  package.json  vite.config.js  node_modules/  ...
├── scripts/                   # 开发机部署工具（不传服务器）
│   ├── quant-deploy.sh        # 有权限部署工具
│   ├── deploy-server.sh       # 便捷更新后端
│   ├── deploy-web.sh          # 便捷更新前端
│   ├── init-db.sh             # 本地 dev
│   ├── init-valkey.sh         # 本地 dev
│   └── verify.sh              # 本地 dev
├── docs/ flow/ DEPLOY.md CLAUDE.md .gitignore
```

## 文件移动清单（开发机）

```bash
cd ~/Projects/quant
mkdir -p server/scripts web

# 1. 前端出 src（含 node_modules，同盘 mv 快）
mv src/web_ui web/

# 2. 后端代码进 server
mv src server/src

# 3. 后端配置/依赖进 server
mv requirements.txt .env.example server/
mv .env server/.env                   # 本地 .env（trust 免密）

# 4. 服务器需要的脚本进 server/scripts
mv scripts/init-seed.sql server/scripts/
mv scripts/systemd server/scripts/systemd

# 5. scripts/ 保留开发机部署工具 + 本地 dev 脚本（不动）

# 6. 本地 venv 重建（venv 含绝对路径，不能 mv；删了在 server/ 下重建）
rm -rf venv
cd server && python3.10 -m venv venv && source venv/bin/activate
pip install -i https://pypi.tuna.tsinghua.edu.cn/simple -r requirements.txt
```

> venv 重建耗时（vnpy 生态），一次成本。node_modules 同盘 mv 通常可用，若 vite 启动失败 `cd web && npm install` 重建。

## 部署脚本改动

### `scripts/deploy-server.sh`（简化：一个 deploy 传整个 server/）
```bash
LOCAL="$(cd "$SCRIPT_DIR/../server" && pwd)"
REMOTE="/data/websites/snailtrail.org/quant/server"
EXCLUDES=(--exclude .env --exclude venv/ --exclude __pycache__/ --exclude '*.pyc' --exclude .pytest_cache/)
# 一个 deploy 搞定（server/ 整体），不再分 src/scripts/requirements.txt 三次
... deploy "$LOCAL" "$REMOTE" "${EXCLUDES[@]}" restart-server restart-celery
```

### `scripts/deploy-web.sh`
```bash
(cd "$SCRIPT_DIR/../web" && npm run build)
LOCAL="$(cd "$SCRIPT_DIR/../web/dist" && pwd)"
REMOTE="/data/websites/snailtrail.org/quant/web"
... deploy "$LOCAL" "$REMOTE" restart-web
```

## 不变（关键：服务器无缝）

- **远程结构不变**：`/data/.../quant/server/{src, scripts/init-seed.sql, systemd/, requirements.txt, venv, .env}` + `/data/.../quant/web`。当前服务器已部署的代码仍有效，venv/.env 建设不受影响。
- **systemd WorkingDirectory**（远程 `/data/.../quant/server`）不变。
- **import 路径**（`src.web_api.main`）不变 -- WorkingDirectory=server，python path 含 `src/`。
- **服务器 venv/.env**（待建）-- 并行进行，不冲突。

下次 `deploy-server.sh` 用新结构（server/ 整体），rsync `--delete` 会清理服务器上旧的 `scripts/deploy-*.sh`（误传的）。

## .env 隔离（本地 vs 服务器，不覆盖）

| 位置 | QUANT_DB_URL | 用途 |
|---|---|---|
| 本地 `server/.env` | `postgresql://quant@127.0.0.1:5432/quant`（trust 免密） | 本地开发 |
| 远程 `.env` | `postgresql://quant:密码@127.0.0.1:5432/quant`（md5） | 生产 |

`deploy-server.sh` 的 `--exclude .env` 保证不覆盖。

## 本地开发启动（路径变）

```bash
# 后端
cd ~/Projects/quant/server && source venv/bin/activate
uvicorn src.web_api.main:app --port 8000    # 或 --reload

# 前端
cd ~/Projects/quant/web && npm run dev    # :5173
```

## 文档更新

- `DEPLOY.md`：部署源路径改 `server/`；本地开发启动改 `cd server` / `cd web`
- `CLAUDE.md` 目录地图：`src/` -> `server/src/`，`src/web_ui/` -> `web/`，`scripts/` 拆分说明
- 记忆 `dev-startup.md`：启动命令路径更新
- `flow/进展.md` + `decisions.md`：记录重构决策

## 验证

1. 本地：`cd server && venv/bin/python -c "from src.web_api.main import app; print('import OK')"`
2. 本地后端启动：`cd server && source venv/bin/activate && uvicorn src.web_api.main:app --port 8000` -> `curl 127.0.0.1:8000/health`
3. 本地前端：`cd web && npm run dev` -> `:5173`
4. 部署：`./scripts/deploy-server.sh` -> 服务器 `ls /data/.../quant/server/scripts/` 只剩 `init-seed.sql + systemd/`（无 deploy-*.sh）

## 本棒范围与不做

**做**：目录重构（移动文件）+ 部署脚本简化 + 文档更新 + 本地 venv 重建。

**不做**：
- 服务器已部署代码不动（结构兼容，下次 deploy 自动同步）。
- 服务器 venv/.env 建设继续（用户并行操作，不阻塞）。
- import 路径不改（保持 `src.web_api`）。

## 风险与回滚

- 文件移动用 `mv`（可逆，出错可 mv 回）。
- venv 重建是唯一不可逆点（删旧 venv），但旧 venv 可重新 `pip install` 恢复。
- 服务器结构不变，重构失败不影响服务器已部署代码。
