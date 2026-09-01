"""阿里云短信发送（批 7 · 告警订阅分发，2026-09-02）。

凭证走 system_config（Web 设置→告警→短信凭证维护，2026-08-14 弃 .env 入库的 smtp 先例）：
    alert_sms_access_key_id / alert_sms_access_key_secret(password 型加密) /
    alert_sms_sign_name / alert_sms_template_code
任一缺失 = NOT_CONFIGURED（API key 申请到位后在配置页一贴即通，零重启）。

协议：dysmsapi RPC 签名 V1（零 SDK，httpx 直调）——参数排序→RFC3986 编码→
`POST&%2F&`+整串二次编码→HMAC-SHA1(secret+"&")→base64。业务错误走 HTTP 400 +
JSON body（按 Code 判定，不 raise_for_status）。reason 只返回稳定 token
（A3-F1/B3-5：异常原文可能含手机号/URL，禁入 dispatch 审计列，只进 journal）。
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import uuid
from datetime import datetime, timezone
from urllib.parse import quote, urlencode

import httpx

logger = logging.getLogger("alert_dispatch")

_API_URL = "https://dysmsapi.aliyuncs.com/"
_CFG_KEYS = ("alert_sms_access_key_id", "alert_sms_access_key_secret",
             "alert_sms_sign_name", "alert_sms_template_code")


def _sms_config() -> dict | None:
    """读 system_config 四键（secret 解密）。未配置返回 None。"""
    try:
        from src.data_platform.db import get_conn
        from src.quant_common.crypto import decrypt
        cfg: dict[str, str] = {}
        with get_conn() as conn:
            cur = conn.execute(
                "SELECT key, value FROM system_config WHERE key LIKE 'alert_sms_%'")
            for k, v in cur.fetchall():
                if v is None or str(v).strip() == "":
                    continue
                cfg[k] = decrypt(str(v)) if k == "alert_sms_access_key_secret" else str(v).strip()
    except Exception as e:
        logger.error("read alert_sms config failed: %s", e)
        return None
    if not all(cfg.get(k) for k in _CFG_KEYS):
        return None
    return cfg


def sms_configured() -> bool:
    return _sms_config() is not None


def _pe(s) -> str:
    """RFC3986 百分号编码（大写十六进制由 quote 保证；-_.~ 不编码）。"""
    return quote(str(s), safe="-_.~")


def send_sms(phone: str, level: str, title: str) -> tuple[bool, str]:
    """发送一条告警短信。返回 (ok, reason_token)——reason 只允许稳定枚举，原文进 journal。"""
    cfg = _sms_config()
    if not cfg:
        return False, "not_configured"
    params: dict[str, str] = {
        "Action": "SendSms",
        "Version": "2017-05-25",
        "Format": "JSON",
        "AccessKeyId": cfg["alert_sms_access_key_id"],
        "SignatureMethod": "HMAC-SHA1",
        "SignatureVersion": "1.0",
        "SignatureNonce": uuid.uuid4().hex,
        "Timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "PhoneNumbers": phone,
        "SignName": cfg["alert_sms_sign_name"],
        "TemplateCode": cfg["alert_sms_template_code"],
        # 整体 json.dumps 构造，禁 f-string 拼 JSON（引号/特殊字符注入面）
        "TemplateParam": json.dumps({"level": level, "title": str(title)[:20]}, ensure_ascii=False),
    }
    canonical = urlencode(sorted(params.items()), safe="-_.~", quote_via=quote)
    string_to_sign = "POST&" + _pe("/") + "&" + _pe(canonical)
    signature = base64.b64encode(
        hmac.new((cfg["alert_sms_access_key_secret"] + "&").encode("utf-8"),
                 string_to_sign.encode("utf-8"), hashlib.sha1).digest()).decode()
    params["Signature"] = signature
    try:
        r = httpx.post(_API_URL, data=params, timeout=(3, 10))
        body = r.json()   # 业务错误 = HTTP 400 + JSON {"Code": "..."}，不能 raise_for_status
        if body.get("Code") == "OK":
            return True, "ok"
        logger.warning("aliyun sms Code=%s Message=%s", body.get("Code"), str(body.get("Message", ""))[:120])
        code = str(body.get("Code", "UNKNOWN")).replace(" ", "_")
        return False, f"ALIYUN_{code[:24]}"
    except (httpx.HTTPError, ValueError) as e:
        logger.warning("aliyun sms send failed: %s", e)
        return False, "timeout"
