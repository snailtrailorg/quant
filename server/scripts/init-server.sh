#!/bin/bash
# ====================================================================
# 服务器环境初始化（装完 PG/Redis/Nginx/python3.11 后跑，建库/角色/用户/目录）
# 用法（服务器 michael 用户，需 sudo）:
#   sudo bash init-server.sh
# 幂等：可重复跑（已存在不破坏，密码会重置）
# ====================================================================
set -euo pipefail

echo "=== 1. 建 quant 角色 + 库（md5 密码） ==="
# 生成密码
PGPWD=$(openssl rand -hex 16)

# 建/改 quant 角色（幂等）
sudo -u postgres psql <<SQL
DO \$\$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname='quant') THEN
        CREATE ROLE quant WITH LOGIN PASSWORD '$PGPWD';
    ELSE
        ALTER ROLE quant WITH LOGIN PASSWORD '$PGPWD';
    END IF;
END
\$\$;
SQL

# 建 quant 库（已存在则跳过）
if sudo -u postgres psql -tAc "SELECT 1 FROM pg_database WHERE datname='quant'" | grep -q 1; then
    echo "quant 库已存在，跳过创建"
else
    sudo -u postgres psql -c "CREATE DATABASE quant OWNER quant"
fi
sudo -u postgres psql -c "GRANT ALL ON DATABASE quant TO quant"
sudo -u postgres psql -d quant -c "GRANT ALL ON SCHEMA public TO quant"

# 密码存 root 家目录（方便后续查）+ 终端输出
echo "$PGPWD" | sudo tee /root/quant-db-password.txt > /dev/null
sudo chmod 600 /root/quant-db-password.txt
echo ""
echo "========================================"
echo "  quant 数据库密码: $PGPWD"
echo "  已存 /root/quant-db-password.txt（root 可读）"
echo "  .env 的 QUANT_DB_URL 用: postgresql://quant:$PGPWD@127.0.0.1:5432/quant"
echo "========================================"
echo ""

echo "=== 2. 启动 Redis ==="
sudo systemctl enable --now redis
redis-cli ping

echo ""
echo "=== 3. 建 quant 系统用户 + 目录 ==="
# quant 用户
if id quant &>/dev/null; then
    echo "quant 用户已存在"
else
    sudo useradd -m quant
    echo "已建 quant 用户"
fi
# 目录
sudo mkdir -p /data/websites/snailtrail.cc/quant/{server,web} /var/log/quant
sudo chown -R quant:quant /data/websites/snailtrail.cc/quant /var/log/quant
# nginx 读 web 静态文件
sudo usermod -a -G quant nginx 2>/dev/null || true
sudo chmod 750 /data/websites/snailtrail.cc/quant /data/websites/snailtrail.cc/quant/web

echo ""
echo "=== 4. 验证 ==="
# quant 密码连 quant 库（TCP scram）
PGPASSWORD="$PGPWD" psql -U quant -h 127.0.0.1 -d quant -c "SELECT current_user, current_database();"
# Redis
redis-cli ping
# quant 用户 + 目录
id quant
ls -ld /data/websites/snailtrail.cc/quant /data/websites/snailtrail.cc/quant/server /data/websites/snailtrail.cc/quant/web

echo ""
echo "✅ 服务器环境初始化完成"
echo "下一步：部署 quant 代码（开发机跑 ./scripts/deploy-server.sh）"
