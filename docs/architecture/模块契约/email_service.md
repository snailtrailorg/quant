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
email_outbox（读写）；system_config（SMTP 配置）
## 不变量
邮件"发送成功"≠送达（踩坑记录有档）——outbox+重试+last_error 是闭环三件套
