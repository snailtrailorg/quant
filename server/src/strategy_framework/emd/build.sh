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

# 运行时 libstdc++：gcc-toolset 编译的 .so 需 GLIBCXX 3.4.29+，服务器系统 libstdc++（GCC 10.2）
# 仅 3.4.28——rpath 追加 gcc-toolset 的 libstdc++（.so 自足，免 systemd LD_LIBRARY_PATH）。
# 本地（Fedora）该路径不存在，动态链接器忽略、fallback 系统 libstdc++，无副作用。
$GXX -O3 -shared -std=c++11 -fPIC \
  -I vendor/emt/include \
  $INCLUDES $PYINC \
  src/strategy_framework/emd/bind_quote.cpp \
  -L vendor/emt/lib \
  -Wl,-rpath,'$ORIGIN/../../../vendor/emt/lib:/opt/rh/gcc-toolset-13/root/usr/lib64' \
  -lemt_quote_api -lemt_api \
  -o "src/strategy_framework/emd/emd_quote_api${EXT}"

echo "✓ 编译完成：src/strategy_framework/emd/emd_quote_api${EXT}"
