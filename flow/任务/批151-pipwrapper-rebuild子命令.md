# 批 151 · pip-wrapper 扩 rebuild 子命令（staging venv 3.11 升级正道）

> 立项 2026-10-26（用户裁定 A2）。来源＝批 150 彩排红：staging shared/venv=3.10 拒装 numpy 2.4.6（Requires-Python≥3.11）；批 149 漏项「staging venv 重建」现架构无通道（deploy sudo 白名单 6 wrapper 均无此能力，shared/ 目录 quant:quant 700）。
> 关联：批 149（dev 3.11 迁移）· 批 150（lock 前滚 7 包，彩排红挂起中）· A06 §3.5/§9（装位通道/炸半径）

## 目标

quant-pip-wrapper 新增 `rebuild` 子命令：以 quant 身份重建 shared/venv（备份旧 venv → 新解释器 venv → upgrade-tools → 按指定 release_id 的 lock 装配）——staging/prod venv 解释器升级有正道通道，批 150 解卡。

## 现状（已实证 2026-10-26）

| # | 事实 | 锚点 |
|---|---|---|
| 1 | 系统 python3.11 已装（用户手动 dnf，3.11.17，deploy 可达 /usr/bin/python3.11） | ssh 实测 |
| 2 | staging shared/venv=3.10；prod=3.11.13（prod 不需重建） | 彩排 stage5 报错；记忆 server-info |
| 3 | wrapper 现子命令 freeze/upgrade-tools/dry-run/install；严参校验白名单 | wrappers/quant-pip-wrapper:35 |
| 4 | 改 wrapper=改提权面：不进 release 管道，须 **michael 重装位**（staging+prod）；sudoers 三别名不含新命令名（子命令级扩展无 sudoers 面——同一 wrapper 路径） | A06 §3.5/§9 |
| 5 | shared/venv 改动结构上不可指纹 ⇒ release 侧 `-e force_full=true` | A06 §9 表 |
| 6 | lock 路径重写 sed + deps/ 校验逻辑现成可复用 | wrapper:57-66 |

## 依赖（就绪）

系统 python3.11 已装（事实 1）；批 150 lock 已提交（1364f25）。

## 引用

- A06 §3.5 装位通道（michael 人工出场条件）+ §9 炸半径表「改 wrapper 行」
- 批 125（wrapper file:// 重写无条件化的先例——rebuild 装配复用同链）

## 产出

1. **wrapper 扩子命令** `rebuild <python_path> <release_id>`：
   - 严参：python_path 须匹配 ^/usr/bin/python3\.[0-9]+$（防任意解释器注入）；release_id 同现有正则
   - 步骤：①mv venv → venv.bak-$(date +%s)（同目录备份，回滚=rename 回）②$python_path -m venv venv ③新 venv pip install -U pip setuptools wheel ④复用 install 分支全链（lock 重写+hash 装配+孤儿清理+dev 段）
   - 失败语义：任一步非零即退出（venv 半建态由 .bak 兜底回滚——banner 提示手动 rename）
2. **bootstrap 装位清单核对**：wrapper 内容改=重装位（michael 通道 staging+prod 各一次）；sudoers 无需动（无新文件）
3. **测试**：test_deploy_blast_radius.py 过 + 新增 wrapper 单测（参数校验/白名单/rebuild 步骤顺序 mock）
4. **文档**：A06 §3.5 补 rebuild 子命令行；DEPLOY.md 速查加一行「venv 解释器升级：quant-pip-wrapper rebuild /usr/bin/python3.11 <release_id>（装位后可用）」

## 限定范围

改：deploy/wrappers/quant-pip-wrapper（一文件）、docs/architecture/A06-部署管道架构.md（§3.5 一行）、deploy/DEPLOY.md（一行）、server/tests/ 新增 wrapper 测试。**不碰**：sudoers 模板、bootstrap 结构（wrapper 文件级装位不变）、release.yml。

## 接口契约

`quant-pip-wrapper rebuild <python_path> <release_id>` → exit 0=重建+装配完成；非零=失败（venv.bak-* 在位可回滚）。写路径：shared/venv（重建）、shared/venv.bak-*（备份）、/tmp/quant-req-*.lock（重写副本）。

## 验收标准

1. 新单测+blast_radius 全绿；全量 pytest 绿
2. michael 重装位 staging 后：`sudo -u quant /usr/local/sbin/quant-pip-wrapper rebuild /usr/bin/python3.11 202610261519-1364f25` 成功，staging venv→3.11.17
3. staging venv/bin/python --version=3.11.x；pip freeze 按 lock 全装
4. 批 150 彩排重跑绿（stage5 numpy 2.4.6 可装）
5. prod 重装位（michael）后 wrapper 在位检查（release preflight 段）过——prod venv 不重建（3.11.13 已达标）

## mock 方式

wrapper 单测：mock bash 命令执行序列断言步骤顺序与参数校验（先例：test_deploy_dnf_wrapper.py 形态）。
