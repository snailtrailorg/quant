#!/bin/bash
# ====================================================================
# build-xtp.sh - 编译安装 vnpy_xtp（XTP Python 绑定）
# 前置：SDK 已部署到 server/vendor/xtp/（deploy 随代码传）
# 用法（服务器 michael/root 用户，有 sudo）:
#   bash scripts/build-xtp.sh
# ====================================================================
set -euo pipefail

SERVER_DIR="/data/websites/snailtrail.cc/quant/server"
VENV="$SERVER_DIR/venv"
XTP_DIR="$SERVER_DIR/vendor/xtp"
PY="python3.11"   # 写死 python3.11，项目统一

echo "=== vnpy_xtp 编译安装 ==="
echo "SDK: $XTP_DIR"
echo "Python: $PY"
echo ""

# ——— 前置检查 ———

# 1. SDK 检查
if [[ ! -d "$XTP_DIR/include" || -z "$(ls -A "$XTP_DIR/include" 2>/dev/null)" ]]; then
    echo "❌ XTP 头文件不存在: $XTP_DIR/include"
    exit 1
fi
if [[ ! -d "$XTP_DIR/lib" || -z "$(ls -A "$XTP_DIR/lib" 2>/dev/null)" ]]; then
    echo "❌ XTP .so 不存在: $XTP_DIR/lib"
    exit 1
fi

# 2. venv 检查
if [[ ! -x "$VENV/bin/pip" ]]; then
    echo "❌ venv/pip 不存在: $VENV/bin/pip"
    exit 1
fi

# 3. python3.11 检查
if ! command -v $PY >/dev/null 2>&1; then
    echo "❌ $PY 不存在，先装: sudo dnf install -y python3.11"
    exit 1
fi

# 4. 编译工具检查（缺啥装啥）
echo "=== 1/4 检查编译依赖 ==="
NEED_INSTALL=()
command -v g++ >/dev/null 2>&1 || NEED_INSTALL+=("gcc-c++")
PYTHON_H=$(find /usr/include/python3.11* -name "Python.h" 2>/dev/null | head -1)
if [[ -z "$PYTHON_H" ]]; then
    NEED_INSTALL+=("python3.11-devel")
fi
if [[ ${#NEED_INSTALL[@]} -gt 0 ]]; then
    echo "缺依赖: ${NEED_INSTALL[*]}，正在安装..."
    sudo dnf install -y "${NEED_INSTALL[@]}"
else
    echo "✅ 编译依赖已就绪"
fi

# 5. vnpy_xtp 已装检查（幂等）
echo ""
echo "=== 2/4 检查 vnpy_xtp 是否已装 ==="
if sudo -u quant bash -c "export LD_LIBRARY_PATH=$XTP_DIR/lib:\$LD_LIBRARY_PATH; $VENV/bin/python -c 'import vnpy_xtp' 2>/dev/null"; then
    echo "✅ vnpy_xtp 已装，跳过编译"
else
    echo "未装，开始编译..."

    # 6. 注册 .so 路径
    echo ""
    echo "=== 3/4 注册 .so 路径 ==="
    if ! grep -q "$XTP_DIR/lib" /etc/ld.so.conf.d/xtp.conf 2>/dev/null; then
        sudo sh -c "echo $XTP_DIR/lib > /etc/ld.so.conf.d/xtp.conf"
        sudo ldconfig
        echo "✅ ldconfig 注册"
    else
        echo "✅ ldconfig 已注册"
    fi

    # 7. 装构建依赖（pybind11/meson/ninja）
    # vnpy_xtp 的 meson.build 硬编码用 /bin/python3 找 pybind11
    # 写死 python3.11：symlink /bin/python3 -> python3.11 + 系统 python3.11 装 pybind11
    echo ""
    echo "=== 4/4 编译 vnpy_xtp ==="
    echo "  装 pybind11/meson/ninja..."
    sudo -u quant "$VENV/bin/pip" install pybind11 meson ninja
    sudo $PY -m pip install pybind11
    # symlink /bin/python3 -> python3.11（meson 硬编码用 /bin/python3）
    sudo ln -sf /usr/bin/$PY /bin/python3
    # 编译 vnpy_xtp
    # ⚠️ -j1 必须：vnpy_xtp 的 vnxtpmd/vnxtptd.cpp 是巨文件，ninja 默认全核并行 → cc1plus OOM 被 Kill。
    #    串行 -j1 降内存峰值。CPATH/LIBRARY_PATH 让编译器找到 XTP 头+库（ldconfig 已注册 .so，保险带）
    sudo -u quant bash -c "export XTP_HOME=$XTP_DIR; export CPATH=$XTP_DIR/include:\$CPATH; export LIBRARY_PATH=$XTP_DIR/lib:\$LIBRARY_PATH; export LD_LIBRARY_PATH=$XTP_DIR/lib:\$LD_LIBRARY_PATH; $VENV/bin/pip install --config-settings=compile-args=-j1 vnpy_xtp"
    echo "✅ vnpy_xtp 编译安装完成"
fi

# 8. 验证
echo ""
echo "=== 验证 ==="
# vnpy_xtp __init__ 显式 import importlib_metadata 但漏声明依赖，必须补装
sudo -u quant "$VENV/bin/pip" install importlib_metadata
# vnpy 4.0 网关模式：顶层只导出 XtpGateway（旧的 XtpTdApi/XtpMdApi 已移到 api 子模块）
sudo -u quant bash -c "export LD_LIBRARY_PATH=$XTP_DIR/lib:\$LD_LIBRARY_PATH; $VENV/bin/python -c 'from vnpy_xtp import XtpGateway; print(\"✅ vnpy_xtp import OK\")'"

echo ""
echo "=== 完成 ==="
echo "SDK: $XTP_DIR"
echo "测试账户: 253191001822 / Xkih9pt2"
echo "交易: 122.112.139.0:6102 / 行情: 119.3.103.38:6002"
