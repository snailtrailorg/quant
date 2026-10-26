# 批 152 · vnpy-xtp 正位入 lock（暗依赖清零）

> 立项 2026-10-26（用户裁定）。来源＝批 151 三连爆病理终章：vnpy_xtp 为 lock 外手装（pypi 只发 sdist，--generate-hashes 下 pip-compile 拖死 1h+ 实证），venv 重建即丢（dev/staging 各爆一次）。

## 方案

批 125 vnpy fork 同款形态：本地构建 cp311 wheel 入 deps/，requirements 用 file 协议引用——hash 从本地文件算（pypi sdist hash 拉取问题消失），部署面 wrapper 的 file:// 重写正则泛匹配自动覆盖。

## 实施记录（2026-10-26）

1. pip wheel --no-deps 构建 vnpy-xtp==2.2.32.2.3 → deps/vnpy_xtp-2.2.32.2.3-cp311-cp311-linux_x86_64.whl（3.11 工具链一次构建）
2. requirements.txt 注释行改 file 引用；lock-sync 五守卫全过
3. lock :3539 vnpy-xtp @ file 引用带 hash；一致性闸门 20 绿 + dry-run --require-hashes 全解析（hash 校验真过）
4. 全量回归 2851 绿

## 限定范围

requirements.txt/requirements.lock/deps 新 wheel。不碰：wrapper（重写正则已泛匹配）、deploy_slices/whitelist（deps 已在）。

## 验收（随下次 release）

- staging/prod stage5 预检+装配含 vnpy-xtp wheel（重写后 release 树路径）
- staging 重建 venv 不再需手拷 vnpy_xtp

## 回滚

git revert 两文件+删 wheel。

## mock 方式

无——依赖批，lock 闸门+回归+彩排即载体。
