#!/usr/bin/env bash
# EMQ 行情 pybind11 绑定编译（批 63 Phase B P1/P2）
#
# 用法：bash server/src/strategy_framework/emd/build.sh
# - 本地（Fedora，Python 3.10）编译验证用；服务器（al8，Python 3.11）部署时
#   在服务器 venv 里重跑本脚本（pybind11 绑定 .so 按解释器 ABI 编译，须对齐）。
# - 前置：server/venv 已 pip install pybind11；vendor/emt/{include,lib} 已就位
#   （头文件提交 git，.so 部署时放服务器）。
set -euo pipefail
cd "$(dirname "$0")/../../.."          # → server/ 目录

GXX=${GXX:-g++}
PY=${PY:-venv/bin/python}

INCLUDES=$($PY -m pybind11 --includes)
PYINC=$($PY -c "import sysconfig; print('-I' + sysconfig.get_paths()['include'])")
EXT=$($PY -c "import sysconfig; print(sysconfig.get_config_var('EXT_SUFFIX'))")

$GXX -O3 -shared -std=c++11 -fPIC \
  -I vendor/emt/include \
  $INCLUDES $PYINC \
  src/strategy_framework/emd/bind_quote.cpp \
  -L vendor/emt/lib \
  -lemt_quote_api -lemt_api \
  -o "src/strategy_framework/emd/emd_quote_api${EXT}"

echo "✓ 编译完成：src/strategy_framework/emd/emd_quote_api${EXT}"
