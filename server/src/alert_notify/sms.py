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
# 批43：凭证多行化（sms_provider 表，position 单层序）——system_config 五键已随 0086 迁移退役
_MAX_ATTEMPTS = 3   # failover 尝试行数上限（时序预算：3×(2+5)=21s < soft_time_limit 30）


def _available_providers(require_verify_tpl: bool = False) -> list[dict]:
    """启用行按 (position ASC, id ASC) 返回（secret 解密；解密失败跳行+warn）。

    批43 v3 候选集对称：四件套（enabled+ak_id+secret+sign）为基础；
    告警候选另需 alert_template_code、验证码候选另需 verify_template_code——两集独立。"""
    from src.quant_common.crypto import decrypt
    try:
        from src.data_platform.db import get_conn
        with get_conn() as conn:
            cur = conn.execute(
                "SELECT id, name, access_key_id, access_key_secret, sign_name, "
                "alert_template_code, verify_template_code FROM sms_provider "
                "WHERE enabled AND COALESCE(trim(access_key_id),'')<>'' "
                "AND COALESCE(trim(access_key_secret),'')<>'' "
                "AND COALESCE(trim(sign_name),'')<>'' ORDER BY position, id")
            rows = cur.fetchall()
    except Exception as e:
        logger.error("read sms_provider failed: %s", e)
        return []
    out = []
    for rid, name, ak, enc, sign, alert_tpl, verify_tpl in rows:
        tpl = verify_tpl if require_verify_tpl else alert_tpl
        if not (tpl or "").strip():
            continue   # 对应模板缺失=不进该候选集（告警/验证码独立判定）
        try:
            secret = decrypt(str(enc))
        except Exception as e:
            logger.warning("sms_provider %s secret 解密失败跳行: %s", rid, e)
            continue
        out.append({"id": rid, "name": name, "access_key_id": str(ak).strip(),
                    "access_key_secret": secret, "sign_name": str(sign).strip(),
                    "template_code": tpl.strip()})
    return out


def _sms_config() -> dict | None:
    """批43：告警面最优行（failover 首选）——兼容旧调用形态。"""
    rows = _available_providers(require_verify_tpl=False)
    return rows[0] if rows else None


def sms_configured() -> bool:
    """告警候选集非空（批43 v3 语义——告警面判定）。"""
    return bool(_available_providers(require_verify_tpl=False))


def _pe(s) -> str:
    """RFC3986 百分号编码（大写十六进制由 quote 保证；-_.~ 不编码）。"""
    return quote(str(s), safe="-_.~")


def _aliyun_send(cfg: dict, params: dict[str, str], timeout: tuple = (3, 10)) -> tuple[bool, str]:
    """dysmsapi RPC 签名 V1 发送（批30 抽公共——告警/验证码两模板同通道）。"""
    canonical = urlencode(sorted(params.items()), safe="-_.~", quote_via=quote)
    string_to_sign = "POST&" + _pe("/") + "&" + _pe(canonical)
    signature = base64.b64encode(
        hmac.new((cfg["access_key_secret"] + "&").encode("utf-8"),
                 string_to_sign.encode("utf-8"), hashlib.sha1).digest()).decode()
    params["Signature"] = signature
    try:
        r = httpx.post(_API_URL, data=params, timeout=timeout)
        body = r.json()   # 业务错误 = HTTP 400 + JSON {"Code": "..."}，不能 raise_for_status
        if body.get("Code") == "OK":
            return True, "ok"
        logger.warning("aliyun sms Code=%s Message=%s", body.get("Code"), str(body.get("Message", ""))[:120])
        code = str(body.get("Code", "UNKNOWN")).replace(" ", "_")
        return False, f"ALIYUN_{code[:24]}"
    except httpx.ConnectError as e:
        # 批43 v3：未送达（连接都没建上）=确定性失败 → unreachable（failover 可切次行）
        logger.warning("aliyun sms connect failed: %s", e)
        return False, "unreachable"
    except (httpx.HTTPError, ValueError) as e:
        # 超时类（读/写超时——结局模糊可能已送达）与畸形响应 → timeout（failover 不切，防双发）
        logger.warning("aliyun sms send failed: %s", e)
        return False, "timeout"


def _base_params(cfg: dict, phone: str, template_code: str, template_param: dict) -> dict[str, str]:
    return {
        "Action": "SendSms",
        "Version": "2017-05-25",
        "Format": "JSON",
        "AccessKeyId": cfg["access_key_id"],
        "SignatureMethod": "HMAC-SHA1",
        "SignatureVersion": "1.0",
        "SignatureNonce": uuid.uuid4().hex,
        "Timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "PhoneNumbers": phone,
        "SignName": cfg["sign_name"],
        "TemplateCode": template_code,
        # 整体 json.dumps 构造，禁 f-string 拼 JSON（引号/特殊字符注入面）
        "TemplateParam": json.dumps(template_param, ensure_ascii=False),
    }


def _send_with_failover(phone: str, require_verify_tpl: bool, tpl_params: dict) -> tuple[bool, str]:
    """批43 failover（v3 分类表）：逐行尝试（上限 _MAX_ATTEMPTS，per-row timeout (2,5)）。

    分类（三轮盲审 P1-2/成文裁定）：
    - 业务 Code≠OK / HTTP 4xx 5xx / ConnectError（未送达）→ **切次行**（确定性失败或必败连接）
    - Read/WriteTimeout（结局模糊——可能已送达）→ **不切**（批7 单次尝试铁律：防双发计费）
    reason 取最后尝试行；零候选=not_configured/verify 面专用 verify_template_missing。"""
    rows = _available_providers(require_verify_tpl=require_verify_tpl)
    if not rows:
        return False, "verify_template_missing" if require_verify_tpl else "not_configured"
    reason = "not_configured"
    for row in rows[:_MAX_ATTEMPTS]:
        params = _base_params(row, phone, row["template_code"], tpl_params)
        ok, reason = _aliyun_send(row, params, timeout=(2, 5))
        if ok:
            return True, "ok"
        if reason == "timeout":
            break   # 模糊结局不切（双发防线）
    return False, reason


def send_sms(phone: str, level: str, title: str) -> tuple[bool, str]:
    """发送一条告警短信。返回 (ok, reason_token)——reason 只允许稳定枚举，原文进 journal。
    批40：截断上移 dispatch._render_sms（内容层）；批43：多行 failover。签名不动（契约钉）。"""
    return _send_with_failover(phone, False, {"level": level, "title": str(title)})


def send_sms_code(phone: str, code: str) -> tuple[bool, str]:
    """批30：发手机号修改验证码短信。批43：多行 failover（验证码候选集=四件套+verify_tpl，
    与告警候选独立——缺验证码模板的行可发告警不进验证码候选）。签名不动（契约钉）。"""
    return _send_with_failover(phone, True, {"code": code})
