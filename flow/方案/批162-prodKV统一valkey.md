# 批 162 · prod KV 统一 valkey（redis 6.2 → valkey 8.0.11）

> 立项 2026-11-02（用户裁定挂待办，择期执行——不专门停产，等顺路窗）。
> 背景：三环境 KV 真身查实（2026-11-02）——dev+staging 共用本机 valkey 8.1.10（fedora.snailtrail.org pid 1755 同进程），**prod 独用 redis 6.2.24（al8 包，EPEL 有 valkey 8.0.11-1.el8 现成可装）**。协议兼容（redis-py 8.1 两方言通用，Streams xadd/xread/xgroup/xack/xautoclaim 全支持）——今天零功能故障，本批属一致性/EOL/彩排保真度优化，非紧急。

## 一、为什么做（三动机）

1. **redis 6.2 上游 EOL**（2024-10）——安全补丁停更。
2. **彩排保真度缺口**：staging 验的是 valkey 行为，prod 跑的是 redis——Streams 消费语义边缘差异测不到。
3. **文档失真**：CLAUDE.md/server-info 记「Valkey」，prod 实况 redis——名实不符。

## 一点五、迁移对象定谳（2026-11-02 michael 终判）

- 库全景（michael 实测 2026-11-02）：**db4=业务+feishu 长连接（297 键）**（VALKEY_URL/FEISHU_VALKEY_URL 同库）、**db5=celery broker（27 键**：队列+unacked+kombu 位点，CELERY_BROKER_URL=.../5）、db0 空（DEFAULT_VALKEY_URL 备而未用）。
- 之前「裸连读 None/db0 空」之谜终版：同一实例、库号看错（redis.Redis() 默认 db0）——非多实例。
- 迁移=**全库 RDB 拷贝**（db4+db5 天然覆盖）；对拍清单含 db5 队列键；应用侧零改动（broker/业务两个 URL 都不动）。
- RDB 迁移天然全库拷贝，方案不变；**一切验证命令必须 -n 4**。
- 连带勘误：app.py:22 注释「broker/backend 直连 VALKEY_URL=db0」为陈旧注释（实际 db4）——随批 163 顺手修，不阻塞本批。

## 二、迁移窗口逐步执行清单（盘后 15:05 后 · 三阶段 · 每步带验证标准与回滚点）

> 执行主体：助手（ansible 通道+API 验证）；michael 仅 sudoers 面四条（装包/停 redis/起 valkey/降级 redis）。
> 原则：每步验证绿才进下一步；任一步红走该步标注的回滚动作。窗内应用全停=无写入，RDB 双向兼容。

### 阶段 0 · 窗前准备（提前 1-2 天，零停机）

| # | 动作 | 执行者 | 验证标准 |
|---|---|---|---|
| 0.1 | prod 装 valkey（EPEL）：`dnf install valkey` | michael | `rpm -q valkey` = 8.0.11-1.el8；`systemctl is-enabled valkey` = disabled（装包不起服务） |
| 0.2 | valkey.conf 对齐稿已生成 ✅ 2026-11-02：逐键对照完成（redis 20 行实读 vs valkey 出厂）——仅 bind 同值/port 6380/dir /var/lib/valkey 三处差异+logfile 换目录；单元形态定谳（ExecStart 自带 --supervised systemd+User=valkey+RuntimeDirectory=valkey——conf 不写 supervised/pidfile）；**conf 终版已备（十行）**窗内 michael 首条即 cp | 助手 | conf 十行定稿（对齐稿存 batch162 工作档）；数据面：prod dump.rdb 5.9MB 迁移秒级 |
| 0.3 | 扫描跟改提交+上产：security_collector _rpm_map 加 "valkey": "redis"（组件键延续） | 助手 | 回归绿+staging 彩排+prod 发布；下轮 scan 组件面 KV 行仍出（redis 6.2.24） |
| 0.4 | dev 演练 RDB 路径：本机 valkey 导出 RDB→新起 valkey 加载→dbsize+抽样键对拍 | 助手 | 演练脚本三步全过（ dbsize 相等/抽样 20 键值相等/TTL 数相等） |
| 0.5 | 切换窗预演（staging 不动——staging 已是 valkey；本步=清单桌面走查+回滚路径确认） | 助手 | 清单每步有验证标准+回滚动作，无开放问题 |

### 阶段 1 · 数据迁移（窗内前段，redis 仍在服务）

| # | 动作 | 执行者 | 验证标准 | 回滚点 |
|---|---|---|---|---|
| 1.1 | 波次停应用（按 deploy 既有单元序）：celery beat→workers→md-hub@*→IM pool→web-api | 助手（ansible） | `systemctl is-active` 全 inactive；journal 无新任务 received | 无（可原地逐个重启） |
| 1.2 | 确认零写入：`redis-cli client list` 仅本地残余连接+`info stats` 的 instantaneous_ops_per_sec≈0 | 助手 | 两次采样（间隔 5s）ops 均 <5 | — |
| 1.3 | `redis-cli bgsave` + 等 `last_save_time` 更新 | 助手 | `info persistence` rdb_last_bgsave_status=ok | — |
| 1.4 | RDB 拷入 valkey 数据目录：`cp /var/lib/redis/dump.rdb /var/lib/valkey/dump.rdb` + 属主 valkey:valkey | michael | ls -la 属主/大小一致；md5 与源相等 | — |
| 1.5 | valkey 临时位起服务：`systemctl start valkey`（6380） | michael | `valkey-cli -p 6380 ping` = PONG | stop valkey |
| 1.6 | **键级对拍**（⚠️ 2026-11-02 定谳：**应用业务全在 db4**——VALKEY_URL=redis://127.0.0.1:6379/4，db0 空）：dbsize db4 相等 + 关键键抽样（db4：sec:dep_audit/sec:dep_alert_state/hub:bars* 消费位点/celery 队列键/quant:hb:md-hub:gen）+ 类型与值相等 | 助手 | db4 dbsize 相等；抽样 ≥10 键值全等；stream 的 length 相等 | — |

### 阶段 2 · 切轨与验证（后段）

| # | 动作 | 执行者 | 验证标准 | 回滚点 |
|---|---|---|---|---|
| 2.1 | valkey 停 + conf 改 port 6379 + `systemctl stop redis && systemctl disable redis` + `systemctl start valkey` | michael | `valkey-cli -p 6379 ping`=PONG；`redis-cli ping` 失联（6379 无 redis）；is-enabled redis=disabled | **回滚总闸**：stop valkey→改回 6380→start redis→应用起（数据零损失，30s） |
| 2.2 | 波次起应用（逆序）：web-api→IM pool→md-hub@*→workers→beat | 助手 | 全 active；journal 起动无 traceback；web /readyz ok | — |
| 2.3 | **三链验证**：①celery：beat 派发 broker_health_check（或触发一 security_scan）→worker received+succeeded；②md-hub Streams：`xinfo groups hub:bars:*` 消费组位点在+新 xadd 一条→worker xreadgroup 消费到；③告警链：sec:dep_alert_state 读写正常（触发一 scan 看 B156A 行） | 助手 | 三链各有 journal 证据行 | 同 2.1 总闸 |
| 2.4 | hbcheck 通：`quant-hbcheck`（走 .env VALKEY_URL db4 心跳） | 助手 | exit 0+心跳字段 gen 连续 | — |
| 2.5 | 观察窗 30min：journal error 计数=0+md-hub 心跳 gen 递增+celery 无 unacked 堆积 | 助手 | 三项全绿 | — |
| 2.6 | 收尾：server-info.md/CLAUDE.md KV 行改实况；redis 包**保留**（回滚保险，一个发布周期后卸） | 助手 | 文档 push | — |

### 阶段 3 · 次日自然轮验收

| # | 项 | 验证标准 |
|---|---|---|
| 3.1 | 05:00 dep_audit | journal：心跳行+done 终态+n_pip 354+OS 面含 valkey 包行（rpm 采集）|
| 3.2 | 06:00 security_scan | succeeded+组件面 KV 行= valkey 8.0.11（_rpm_map 跟改生效）+B156A 正常 |
| 3.3 | 批 161 心跳机制新 KV 观察 | sec:dep_audit:beat TTL 26h 生效+三态判定 prev_state=done |

## 三、跟改清单（全引用面 grep 核查 2026-11-02——五面零改+一处跟改）

| 面 | 改动 | 依据 |
|---|---|---|
| requirements.txt / 应用代码 | **零改** | redis-py 8.1 两方言通用；VALKEY_URL 同 port 同 db |
| 部署管道（playbooks/inventory/wrappers） | **零改** | deploy_hub_hb_mode 'redis' 是模式名非引擎绑定；quant-hbcheck 走 redis-py+.env URL |
| systemd 单元 | **零改** | 全部单元不 Requires 具体 KV 服务名（live-task 注释明记） |
| dep_audit（审计） | **零改自动适应** | os_pkg provider 扫 rpm 面：valkey 包自动入审计；redis 卸载后孤儿行走批160 三日退役 |
| **security_collector 组件扫描** | **一处跟改** | _rpm_map（:391）加 "valkey": "redis" 条目（rpm 名变 valkey 后组件键延续）+_LEGACY8 基线核对——不加=组件版本扫描丢 KV 项。随切换窗前提交上产 |
| 配置文件 | 对齐迁移 | /etc/redis.conf → /etc/valkey/valkey.conf（§2.1）；logrotate 随包 |

## 三点五、全面性保证（不现场试错的三道锁）

1. **兼容性不是假设是事实**：dev+staging 的全部开发/测试/彩排数月来都跑在 valkey 8.x 上（2950 测试+历批验证）——「应用代码在 valkey 上工作」已被长期实证，本批唯一的新变量只有「prod 数据迁移」这一件事。
2. **迁移路径 dev 演练**：RDB 导出→valkey 加载→dbsize/键级对拍，先在本机走通（§2.1 前置），不把第一次留给 prod。
3. **回滚 30 秒**：窗内 prod 无写入（应用全停），redis/valkey RDB 双向兼容——valkey 停→redis 起→应用起即回；装包不卸无害。

## 四、验收补充
- 切换后首轮 security_scan 组件面含 KV 版本行（valkey 8.0.11）——_rpm_map 跟改的验证点。

## 五、限定范围
prod 迁移+文档两行；**不动** dev/staging（已是 valkey）、不动应用代码（URL/客户端零改）、不动部署管道单元名（redis.service→valkey.service 需进 A06 §9 波次清单核对一次）。

## 六、执行时机
不专门排产——**顺路窗**：下次本就要停机/大版本发布的盘后窗执行；或 redis 6.2 出新 CVE 时升级为优先。
