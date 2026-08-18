# 盲审 · 模块归位两联（P/Q，2026-08-19）

## P（设计审）：有条件批准 → 3 致命修订全落
- P-F1 feishu_bot→web_api 边清不干净（audit_log 下沉 data_platform 才归零）
- P-F2 _alert 进 quant_common=新上行边（guard 改回调注入+alert_notify 落 safe_notify 收编三处重复）
- P-F3 crypto_utils 不能整文件搬（get_conn 模块级 import；死代码 store_api_key/get_api_key 删除）
- S4 "只许 stdlib"自相矛盾（cryptography/dotenv 白名单入断言）；S5 md_hub 并未"已依赖 strategy_framework"（论据修正）；S6 点名 ~20 个测试 patch 点（最阴险：patch src.web_api.main.get_conn 永远"成功"但拦不住搬走的函数=假绿）；S7 verify_integration.py 漏网消费者

## Q（代码审）：需修后部署 → 全修
- Q-S1 strategy_runner _guard 顶层双定义（手术残体——py_compile 过、测试两版都绿，AST 实锤）
- Q-S2 test_layering 预留豁免=闸门自开洞（"当前无，预留"直接违反 P-F2 铁律）
- Q-nit hub_worker"直连"宣称不实（上一轮 replace 静默未中——git diff 空实证；**教训：s.replace 无 assert 是静默 no-op**）
- 层测试白捡 P 漏项：terms 注册表也寄生 web_api（纯数据已迁 quant_common）

## 终态验收（实测）
层级违规 6→0（豁免外）；双向环 7→1（data_platform↔alert_notify 横向）；md_hub→strategy_runner / scheduler→web_api / feishu_bot→web_api 三条历史边消失；test_layering 4 断言永久守分层；317 绿；生产全组件 active（hub gen=17）
