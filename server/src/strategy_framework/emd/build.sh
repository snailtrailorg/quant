#!/usr/bin/env bash
# EMQ 行情 pybind11 绑定编译（批 63 Phase B P1/P2）
#
# 用法：bash server/src/strategy_framework/emd/build.sh
# - 本地（Fedora，Python 3.10，GCC 14）编译验证用。
# - 服务器（al8，Python 3.11，系统 GCC 8.5）部署时：GCC 8.5 不支持 EMQ 头文件的
#   `EMQ_EXCHANGE_TYPE::EMQ_EXCHANGE_UNKNOWN`（C++23 enum 作用域限定，GCC 11+ 才支持），
#   须用 gcc-toolset-11+ 编译：
#       scl enable gcc-toolset-11 -- bash src/strategy_framework/emd/build.sh
# - 前置：server/venv 已 pip install pybind11；vendor/emt/{include,lib} 已就位
#   （头文件提交 git，.so 部署时放服务器）。
set -euo pipefail
cd "$(dirname "$0")/../../.."          # → server/ 目录

GXX=${GXX:-g++}
PY=${PY:-venv/bin/python}

INCLUDES=$($PY -m pybind11 --includes)
PYINC=$($PY -c "import sysconfig; print('-I' + sysconfig.get_paths()['include'])")
EXT=$($PY -c "import sysconfig; print(sysconfig.get_config_var('EXT_SUFFIX'))")

# 运行时 libstdc++：gcc-toolset 编译默认动态链接其新版 libstdc++，若 .so 引用了
# 高于系统 GCC 8.5（GLIBCXX 3.4.25）的符号版本，运行时需 LD_LIBRARY_PATH 指向
# gcc-toolset 的 libstdc++（bind_quote 仅用 C++11 特性，通常不超 3.4.25，部署时 readelf 复核）。
$GXX -O3 -shared -std=c++11 -fPIC \
  -I vendor/emt/include \
  $INCLUDES $PYINC \
  src/strategy_framework/emd/bind_quote.cpp \
  -L vendor/emt/lib \
  -Wl,-rpath,'$ORIGIN/../../../vendor/emt/lib' \
  -lemt_quote_api -lemt_api \
  -o "src/strategy_framework/emd/emd_quote_api${EXT}"

echo "✓ 编译完成：src/strategy_framework/emd/emd_quote_api${EXT}"
