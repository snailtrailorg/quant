# 模块契约 · quant_common（公共底座，2026-08-19 模块归位新建）

> 本模块的 public API + 依赖 + 被调 + 不变量。任务改本模块前读本文件。
> 配套：`tests/test_layering.py`（分层断言守门）+ `flow/decisions.md` 归位决策。

## 职责
全平台共享基础设施（层 0）。原设计 T06 规划、2026-08-19 落地——此前共享工具寄生
web_api/strategy_runner 导致 6 条层级违规（审计实锤，见 decisions）。

## 文件结构
```
server/src/quant_common/
├── crypto.py    # encrypt/decrypt/mask（从 web_api 归位；死代码 store_api_key/get_api_key 已删）
├── session.py   # in_astock_session / session_edge（从 strategy_runner 归位——hub 曾因此模块级 import runner 连带 vnpy 链）
├── guard.py     # guard(name, alert=None) 回调注入 / sd_notify
└── terms.py     # TERMS / LANG_NAMES i18n 注册表（层测试白捡的寄生项，从 web_api 归位）
```

---

## 一、public API

```python
# crypto.py
encrypt(plaintext: str) -> str          # Fernet；ENCRYPTION_KEY 缺省从 JWT_SECRET 派生（告警提示）
decrypt(ciphertext: str) -> str
mask(key: str, visible=4) -> str

# session.py
in_astock_session(now=None) -> bool     # 931-1130/1301-1500 周一~五；节假日不感知（调用方叠加"今日有 tick"）
session_edge(cur: bool, was: bool) -> bool  # 进入沿——staleness 基线清零专用（S6）

# guard.py
guard(name: str, alert=None)            # handler 守卫：异常拦截不上抛（F-26）；alert 回调由调用方注入
sd_notify(msg: str)                     # systemd WATCHDOG 喂狗（无 NOTIFY_SOCKET 静默）

# terms.py
TERMS / LANG_NAMES / available_langs() / get_terms_items() / get_terms()
```

## 二、依赖
**禁止 import 任何 src.\* 业务模块**（test_layering 断言）；第三方白名单：cryptography、python-dotenv。

## 三、被调（全员）
alert_notify / data_platform / email_service / health_monitor / llm_gateway / md_hub /
strategy_framework / strategy_runner / web_api / feishu_bot——全层引用，故自身必须零上行。

## 四、调用范式（重要）
```python
# 守卫+告警（告警不能住本包——会造上行边）：
from src.quant_common.guard import guard as _guard_base
def _alert(title, body=""): safe_notify("critical", title, body)   # alert_notify.safe_notify
def _guard(name):
    return _guard_base(name, alert=lambda title, body="": _alert(title, body))
    #                     ^ 晚绑定 lambda——保测试 patch 模块级 _alert 语义（Q-S1 教训）
```

## 五、不变量
1. 零业务依赖（test_layering.test_quant_common_purity 锁死）
2. 第三方白名单外禁引入（test_quant_common_third_party_whitelist）
3. alert 永远回调注入，不直接 import
