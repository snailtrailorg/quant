# 批 162 · prod KV 统一 valkey（redis 6.2 → valkey 8.0.11）

> 立项 2026-11-02（用户裁定挂待办，择期执行——不专门停产，等顺路窗）。
> 背景：三环境 KV 真身查实（2026-11-02）——dev+staging 共用本机 valkey 8.1.10（fedora.snailtrail.org pid 1755 同进程），**prod 独用 redis 6.2.24（al8 包，EPEL 有 valkey 8.0.11-1.el8 现成可装）**。协议兼容（redis-py 8.1 两方言通用，Streams xadd/xread/xgroup/xack/xautoclaim 全支持）——今天零功能故障，本批属一致性/EOL/彩排保真度优化，非紧急。

## 一、为什么做（三动机）

1. **redis 6.2 上游 EOL**（2024-10）——安全补丁停更。
2. **彩排保真度缺口**：staging 验的是 valkey 行为，prod 跑的是 redis——Streams 消费语义边缘差异测不到。
3. **文档失真**：CLAUDE.md/server-info 记「Valkey」，prod 实况 redis——名实不符。

## 二、迁移方案（一个盘后窗 ~30min）

### 2.1 前置（窗前完成，零停机）
- prod 装包（EPEL valkey 8.0.11）——装包不起服务。
- 配置：/etc/valkey/valkey.conf 对齐现 redis 配置（bind 127.0.0.1/port 6379 **先改 6380 临时位**/appendonly 与 maxmemory 同值对齐）。
- dev 演练：本机 valkey 同为 8.x，RDB 加载路径 dev 先走一遍（--rdb 导出→valkey 加载→dbsize 对拍）。

### 2.2 切换窗（盘后 15:05 后，~30min）
1. 停 celery beat/worker/md-hub/IM pool/web-api（波次停，按 deploy 八阶段既有单元序）。
2. redis-cli bgsave → RDB 快照落盘。
3. **数据迁移**：RDB 拷给 valkey（同 RDB 格式直接加载）：valkey 以 6380 起→dbsize/关键键对拍（hub:bars 消费位点/sec:* 锁/celery 队列深度）。
4. **切轨**：valkey 停→配置改回 port 6379→redis stop+disable→valkey 起在 6379→应用侧 VALKEY_URL 零改动（同 port 同 db）。
5. 验证三链：celery（beat 派发一任务+worker 消费）/md-hub Streams（xadd→xreadgroup 消费组位点续读）/告警 notify（sec:dep_alert_state 读写）。
6. 绿则 systemctl disable redis；redis 包保留一个发布周期后卸（回滚保险）。

### 2.3 回滚
窗内任一步红：valkey 停→redis 重起 6379→应用重起（RDB 双向兼容——迁移窗内 prod 无写入，数据零损失；装包不卸无害）。

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

## 四、验收
- 三链验证（§2.2.5）全绿+次日 05:00 dep_audit/06:00 scan 自然轮 journal 正常。
- server-info.md/CLAUDE.md KV 行改记实况（prod=valkey 8.0.11，三环境统一）。
- 批 161 心跳机制在新 KV 上观察一轮（beat 键 TTL/三态判定）。

## 五、验收补充
- 切换后首轮 security_scan 组件面含 KV 版本行（valkey 8.0.11）——_rpm_map 跟改的验证点。

## 六、限定范围
prod 迁移+文档两行；**不动** dev/staging（已是 valkey）、不动应用代码（URL/客户端零改）、不动部署管道单元名（redis.service→valkey.service 需进 A06 §9 波次清单核对一次）。

## 七、执行时机
不专门排产——**顺路窗**：下次本就要停机/大版本发布的盘后窗执行；或 redis 6.2 出新 CVE 时升级为优先。
