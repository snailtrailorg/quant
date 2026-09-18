# 模块契约 · email_service（账号生命周期邮件，2026-08-19 从 web_api 独立）

> 配套：`接口契约.md`。原寄生 web_api 导致 scheduler 向上 import（归位消除）。

## 职责
邀请/重置/激活/通知邮件：多语模板（terms 注册表）+ email_outbox 持久发件箱 + 指数退避 +
SMTP SSL/STARTTLS 自适应。**运维告警邮件不在此**（那是 MessageChannel 扩展点，未来实现）。

## public API
```python
send_invite_email(to, token, lang) / send_password_reset_email(...) / send_activation_email(...)
queue_email(to_email, subject, html_body, lang)      # 入 outbox（celery sweep 异步发）
sweep() -> dict                                       # 定时扫描重试（beat：email-outbox-sweep 60s）
normalize_lang(lang) -> str                           # i18n 归一（en 缺省）
```

## 依赖
data_platform（outbox 表）/ alert_notify（失败告警）/ quant_common（crypto+terms）
## 被调
web_api（邀请/重置端点）/ scheduler（sweep beat）
## 读写表
email_outbox（读写）；smtp_provider（批47 多通道配置+密码解密读）；system_config（批47 后仅 smtp_max_attempts 配额键——SMTP 连接配置已迁 smtp_provider 表）
## 不变量
邮件"发送成功"≠送达（踩坑记录有档）——outbox+重试+last_error 是闭环三件套

> **批47（2026-09-18）SMTP 多通道 failover**：`smtp_provider` 表（批43 纯拖拽同模式，行序即 failover 顺序）取代 system_config 六键单实例（迁移 0087 存量搬入+删旧键防双源）；`_providers()`（position 序 enabled+解密）+`_max_attempts()`（system_config `smtp_max_attempts` 默认 3——**总尝试=配额**用户裁定 A）；`_send_email_sync` 改收 provider dict；`_try_row_sync` 状态机——同实例指数退避→配额尽切下一实例（60s 短退避）→轮转一圈 failed+终败通知；outbox 加 `provider_id`/`provider_attempts` 两列（null/悬空回落第一实例）；MAX_ATTEMPTS=6 退役、死行回收独立上限 SWEEP_ABS_LIMIT=30（**不动 provider_attempts**——进程死≠实例故障）；dispatch `_send_email` 对 code=email.failed 跳过 email 通道（防慢速自持续链）。端点族 `/api/smtp-providers` CRUD+reorder（user_mgmt）。
