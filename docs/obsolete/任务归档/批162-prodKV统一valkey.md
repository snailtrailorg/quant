# 批 162 · prod KV 统一 valkey（redis 6.2 → valkey 8.0.11）

> 立项 2026-11-02（用户裁定挂待办，择期执行——不专门停产，等顺路窗）。
> 背景：三环境 KV 真身查实（2026-11-02）——dev+staging 共用本机 valkey 8.1.10（fedora.snailtrail.org pid 1755 同进程），**prod 独用 redis 6.2.24（al8 包，EPEL 有 valkey 8.0.11-1.el8 现成可装）**。协议兼容（redis-py 8.1 两方言通用，Streams xadd/xread/xgroup/xack/xautoclaim 全支持）——今天零功能故障，本批属一致性/EOL/彩排保真度优化，非紧急。

## 一、为什么做（三动机——全部达成）

redis 6.2 EOL（已退役）/彩排保真度（三环境统一 valkey 8.x）/文档名实（已改）。

## 二、执行实录（2026-11-02 15:05-15:45 窗，全部完成）

- 窗前五步（0.1-0.5）11:00-13:00 完成：装包/conf 十行/扫描跟改上产（5af8ad7）/dev 演练/6380 排练。
- 窗内：九单元停（单元名 @quant 后缀踩一脚）→零写入确认（ops=0×2）→bgsave→**对拍拦截一次真险**：BGSAVE 异步返回≠落盘，首拷拿到昨日 RDB（stream 尾差 84 条）——重 bgsave 核时间戳重拷后全对齐（db4 285=285/db5 27=27/db6 差 1=TTL/stream 421=421/值 md5 相等）。
- 切轨：valkey 6379 起+redis stop+disable（RDB 原地留作回滚一个发布周期）。
- 验证：九单元起✓/登录+全 API 200✓/三链（celery 派发消费/beat 周期流水/md-hub 心跳 gen 递增）✓/hbcheck✓/readyz valkey:ok✓/队列零堆积✓。
- 观察窗：调度面持续健康；scan 0f56a47b 的 grype db update 慢路径（16min+，与 KV 无关——心跳哲学：慢≠死）；一条 fail-loud 告警（dep rows 表读瞬态，API 直读同表 864 行正常证实）。

## 三、跟改与文档（已完成）

- security_collector _rpm_map "valkey"→"redis"（5af8ad7 上产——组件键延续）。
- server-info.md KV 行终态（分库实况+BGSAVE 教训）；CLAUDE.md 三环境统一实况（f949c55）。

## 四、遗留

- redis 包一个发布周期后卸（回滚保险期）。
- 次日 05:00 dep_audit/06:00 scan 自然轮=终态复验（audit 走表+缓存与 KV 引擎无关，beat 派发已证通）。

（原方案设计/逐步清单/盲审预置详见 git 史此文件此前版本 aae6618/60a4140/b5bb8b3。）
