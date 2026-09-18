# 批47:SMTP 多实例容灾(批43 短信多行同模式)

> 2026-09-18 用户裁定(反馈池 #6):①SMTP 单实例→多实例+拖动排序 ②failover:单实例缓存队列重发达到最大次数→切下一个;再达最大再切;轮转一圈全失败=彻底失败放弃 ③每次新邮件默认从第一个开始 ④单实例最大重发次数=配置项默认 3。
> **方案盲审 v2**:A(P0×0/P1×4/P2×4)+B(P0×0/P1×3/P2×4)全吸收+**用户裁定配额口径 A:总尝试=配额**(3=该实例共试 3 次后切,键名 `smtp_max_attempts` 消歧)。

## 一、现状(email_service/__init__.py)

- 配置:`_smtp_config()` 读 system_config `smtp_*` 六键(host/port/security/username/password/from)——**单实例**。
- 发送:`_send_email_sync`(smtplib,60s timeout;security auto/ssl/starttls)。
- 发件箱:`email_outbox`(status/attempts/next_attempt_at/last_error)+指数退避(60s→30min 封顶)+**MAX_ATTEMPTS=6 硬编码**+claim 锚+sending 死行回收(批27-1 加固)+beat 每分钟 sweep。
- 终态:`_final_failure_notify`→通知中心 critical。
- 端点:`/api/smtp-config` GET·POST(user_mgmt);前端 SmtpCard 单卡片表单。

## 二、方案(v2)

### A 迁移 0087(双向)

1. **`smtp_provider` 表**:id/name/host/port(int)/security(text auto|ssl|starttls)/username/password(Fernet 密文,搬密文不重加密)/from_addr/position(int)/enabled(bool)/created_at/updated_at。
2. **存量迁移**:smtp_* 六键→表一行(**每键标量子查询**防笛卡尔/零行丢凭证——批43 P0-1 教训;smtp_username 非空才迁;from 空→回落 username)。
3. **email_outbox 加列**:`provider_id`(int null)、`provider_attempts`(int default 0)。
4. system_config:INSERT `smtp_max_attempts='3'`(int;**总尝试=配额**——用户裁定 A)+**upgrade 删旧六键**(`DELETE ... WHERE key LIKE 'smtp_%' AND key != 'smtp_max_attempts'`——防双源,批43 孤儿键全删先例);downgrade=回填六键+删 smtp_max_attempts。
5. **schema_expectations.txt 两处同步**(B-P1-2):+smtp_provider 行;email_outbox 行追加两新列。

### B email_service failover 状态机

- `_providers()` 按 position 读 enabled 行(解密);`_send_email_sync(to,subject,body,provider)` 改收实例 dict;`_smtp_config()` 退役;`_max_attempts()` 读 system_config smtp_max_attempts(缺省 3)。
- **`_try_row_sync` 状态机**(每实例配额=smtp_max_attempts):
  - 实例选择:行 `provider_id` **null 或悬空**(实例已删/停用→不在 enabled 列表)→回落第一实例(A-P1-2)。
  - 失败:`provider_attempts+1 < 配额`→**同实例**指数退避(既有节奏)重试。
  - 配额尽→**切下一实例**(`ORDER BY position` 严格大于当前 position 的首行——禁 position+1,A-P2-2):有→`provider_id=下一/provider_attempts=0/pending`,next_attempt_at=now()+60s(切换短退避);**已最后实例(轮转一圈)→`failed` 终态**+最终失败通知(文案"全部 SMTP 通道重试耗尽")。
  - 成功:sent 路径零改。
  - MAX_ATTEMPTS=6 退役;**死行回收独立绝对上限固定 30**(A-P1-1:回收是纯 SQL 防读动态上限;30 与业务配额独立——配额小则业务 failed 先达,配额大则死行提前 failed=安全方向)+**回收不动 provider_attempts**(A-P2-1 反转:进程死≠实例故障,动则反复重启烧配额误切实例)。
  - claim RETURNING 补 `provider_id, provider_attempts` 两列(A-P2-1)。
- **通知防递归**(A-P1-4):`code='email.failed'` 的外推跳过 email 通道(单实例时代已存在的慢速自持续链,多实例放大——根治)。

### C 端点(system.py,批43 sms 端点族同款)

- `/api/smtp-providers` GET(列表+credentials_set)/POST/PUT(凭证三段语义:缺键=不改/密钥空=不改/明文空=真清空)/DELETE;`/reorder`(全量 id 数组单事务+全集校验+**路由前移防 int 遮蔽**——批43 P0-3)。权限=user_mgmt(与旧端点/集成中心 mail 页签一致,B 员核过)。
- 旧 `/api/smtp-config` GET·POST **退役**;**api.js 死导出 getSmtpConfig/saveSmtpConfig 删**(sendTestEmail 保留——/api/email/test 不退役,B-P1-3)。
- SYSTEM_CONFIG_BOUNDS 加 `smtp_max_attempts: (1, 10, False, False)`;`main.py:134` 注释顺手改。

### D 前端 SmtpCard 重写(批43 骨架+**砍两页签**——B-P2-1)

- 通道表格:拖拽手柄列(menu 图标列头+三横线手柄——批46 形态)/名称/host:port/状态 tag(启用·凭证齐)/操作(编辑+删除)。
- 编辑弹窗:**单页表单**(SMTP 无模板概念):name/host/port/security 三值 radio(auto/ssl/starttls——现款词条 smtp.secAuto 复用)/username/password(show-password)/from;密码三段语义 placeholder 分流(批38)。
- reorder 乐观重排失败重拉;排序说明行。

### E 词条(文案师)+desc

- smtpMaxAttempts desc(批45 模式——"每条通道最多尝试 N 次后换下一条")+SmtpCard 全套;zh/en 双侧+三层守门。

## 三、测试钉(含既有改写清单——B-P1-1)

- **既有改写**:`test_email_outbox.py`——MAX_ATTEMPTS import 退役→ImportError,改 `_max_attempts()`;`test_smtp_config_db_only/unconfigured/port_465_uses_ssl` 三测 patch `_smtp_config`→改 `_providers()` 形状;backoff 曲线/封顶两测保留;`test_max_attempts` 改配额语义。`test_email_sending_recovery.py` 编码时核 sweep 计次适配。
- **新钉**:`_try_row_sync` 四路径(同实例重试/配额尽切下实例 provider_id 变更清零/轮转一圈 failed+通知/成功 sent)+悬空回落第一+回收不动 provider_attempts;端点 CRUD+reorder 全集校验/幽灵 id 拒(挂 test_email_outbox.py 域更近——B-P2-3);迁移双向重放+存量迁移两形态(有/无 smtp_username)。
- 前端:build+守门+dev 手测。

## 四、不做/知情(边界)

- 邮件模板链(send_invite 等零改);告警 email 通道自动受益(dispatch→queue_email 同链)。
- **知情项**(B-P2-2):单实例用户总重试 6→配额 3(两实例×3=6 恰同量级);切实例实际延迟 60-120s(sweep 每分钟+短退避——A-P2-3)。
- per-provider 退避差异化不做。

## 五、验收(用户)

- 集成中心→邮件:多实例表格+拖拽;停一实例→告警邮件切第二实例;全停→failed+铃铛;smtp_max_attempts 可改(默认 3=每通道共试 3 次)。
- 顺手修(P2-4):email test 主题遗留文案(「人工智能开发学习平台」→站名)。

## 六、流程

方案双盲审 A/B ✅(全吸收+口径裁定 A)→编码→代码盲审 A/B→钉+build→staging→prod(盘后窗开放)。

## 七、实施记录(2026-09-18)

- **方案盲审**:A(P1×4:upgrade 删六键/悬空回落/配额口径/递归链)+B(P1×3:测试改写清单/schema_expectations/api.js 死导出)全吸收 v2;用户裁定配额口径 **A=总尝试**(键名 smtp_max_attempts)。
- **代码盲审**:A(P1×1 **同实例退避基数用全局 attempts——切通道后放大成小时级**,改 `_backoff_seconds(prov_att+1)`+新钉/P2×2 docstring+死码)+B(零 P1/P2×3 死键×7+契约滞后)全修。
- **实施坑两枚**(dev 双向重放抓出):`server_default=587/0` int 直传必炸(sa.text 修);downgrade `COALESCE(port,'')` Integer 混型(port::text 修)。
- **验证**:pytest **1170 绿**(test_email_outbox 重写 14 例状态机矩阵+端点钉;sending_recovery 7 元组适配;put_to_post 端点清单换新)/build 绿/守门 1626 对称/**dev 端点手测**(建两通道/reorder 幽灵拒/正常/旧端点 404)/**迁移双向重放**(六键↔表往返+密文平移+best-effort 声明符合)/pyflakes 零新增/schema_expectations 两处/文档回写×3(web_api/email_service/alert_notify)。
- **词条**:文案师两轮(骨架+润色——orderNote 补"每封新邮件从第一条开始"防误读);七死键清(旧 smtp.host/port/security/hint+新三 Field 键)。
