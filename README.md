# 多市场混合量化交易平台

> 个人私有化部署的多市场量化交易平台，覆盖 A 股 / 可转债 / ETF / 加密永续合约，配置驱动策略 + Web 可视化管理 + 飞书移动干预 + AI 辅助研判。

## 功能概览

- **多市场交易**：A 股（中泰 XTP）/ 可转债+ETF（XTP T+0）/ 加密永续（币安/OKX，外部 gate）
- **配置驱动策略**：因子选择 + 权重 + DSL 表达式，Web 端配置不改代码
- **策略注册制**：`@register_strategy` + `from_config` 按 type 分发（双低/CTA 趋势/自定义）
- **回测引擎**：自建纯 Python BacktestEngine，实时可视化（echarts 净值/回撤/交易明细）
- **实盘三级开关**：`.env` 总闸 + Web 分项控制 + 策略级验证，任一关即拒单
- **风控中心**：全局+分市场双层风控，超仓位截断覆写，一键熔断
- **LLM 网关**：DeepSeek/火山方舟/GLM 多模型 fallback 容灾，工具闭环（自然语言查持仓/盈亏/风控）
- **飞书机器人**：ws 长连接，per-机器人角色，AI 动态查询 + 操作确认卡片
- **平台化通用接口**：6 大接口抽象（DataSource/Broker/MessageChannel/Task/RiskRule/LLMProvider），别人配置即接入

## 技术栈

| 类别 | 选型 |
|---|---|
| 后端 | Python 3.10/3.11 + FastAPI + Celery |
| 前端 | Vue3 + Element Plus + echarts + klinecharts |
| 数据库 | PostgreSQL 18 + pgvector |
| 缓存/队列 | Valkey（Redis 协议兼容）|
| 交易内核 | vnpy + vnpy_xtp（中泰 XTP）|
| AI 模型 | DeepSeek / 火山方舟 / GLM（自建网关，不接 OpenAI 运行期）|
| 部署 | venv + systemd（无 Docker）|

## 快速开始

### 前置条件

- Linux 服务器（推荐 4 核 8GB+）
- Python 3.10 或 3.11（不支持 3.14，vnpy 兼容性）
- PostgreSQL 15+ + pgvector
- Redis 或 Valkey
- Node.js 18+（前端构建）

### 安装

```bash
# 1. 克隆
git clone <repo-url> quant && cd quant

# 2. 后端
cd server
python3.11 -m venv venv
source venv/bin/activate
pip install -r requirements.txt

# 3. 配置数据库
bash scripts/init-env.sh   # 交互式生成 .env（数据库密码/Tushare token/AI key）

# 4. 初始化 schema
alembic upgrade head
psql -d quant -f scripts/init-seed.sql   # 种子数据

# 5. 启动后端
uvicorn src.web_api.main:app --port 8000
# 新终端启动 Celery
celery -A src.scheduler.app worker -B -c 2 --loglevel=info

# 6. 前端
cd ../web
npm install && npm run build
# dist/ 部署到 web 服务器（nginx/Apache），反代 /api -> :8000

# 7. 访问
open http://localhost:8000  # 后端 API
# 前端配 nginx 反代后访问前端地址，默认账号 admin/admin123（首次登录请改密码）

# 8. 实盘交易（可选）
# XTP 凭证配置：登录 Web -> 系统设置 -> 交易通道 -> 添加（provider=xtp + 凭证）
# 开启实盘：系统设置 -> 实盘开关 -> 打开对应市场分项
```

详细部署步骤见 [INSTALL.md](INSTALL.md)。

## 项目结构

```
quant/
├── server/               # 后端
│   ├── src/
│   │   ├── web_api/      # FastAPI ~110 端点
│   │   ├── strategy_framework/  # 策略框架（注册制+因子+回测+适配器）
│   ├── strategy_runner/  # 实盘入口（每策略独立子进程）
│   ├── llm_gateway/       # LLM 网关（多模型容灾）
│   ├── data_platform/     # 数据中台（Tushare/AkShare+PG）
│   ├── risk_control/     # 风控中心
│   ├── feishu_bot/        # 飞书机器人
│   ├── scheduler/         # Celery 定时任务
│   └── alert_notify/     # 告警（MessageChannel 接口）
│   ├── migrations/        # alembic schema 版本管理
│   └── tests/            # pytest 单测
├── web/                  # 前端 Vue3
│   └── src/views/        # 34 个页面组件
├── scripts/              # 部署/开发工具
└── docs/architecture/    # 架构文档（总体设计 + 11 模块 + 接口契约 + 13 份模块契约）
```

## 文档

- [总体设计](docs/architecture/00-总体设计.md) — 架构总纲（10 节）
- [安装指南](INSTALL.md) — 全新部署
- [接口契约](docs/architecture/接口契约.md) — 跨模块签名
- [模块契约](docs/architecture/模块契约/) — 逐模块 public API（13 份）

## 许可

私有项目，未公开许可。
