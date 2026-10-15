"""部署树路径解析（批 123）：QUANT_DEPLOY_ROOT 单配置项派生全部子路径。

用户裁定（2026-10-15）两段演进：
1. grype-db 不放 ~（HOME 漂移实害）→ GRYPE_DB_CACHE_DIR 配置化；
2. 「思路一致但想数据与代码分开」→ 部署树本就两层生命周期（releases/=代码
   随发版生灭翻链；shared/+var/=数据跨版本存活）——分离是结构性保证，
   表达上收敛为**一个根配置**：
     QUANT_DEPLOY_ROOT（缺省 /data/websites/snailtrail.cc/quant）
       ├─ shared/   数据+运行时（venv/security/grype-db/static——跨版本）
       └─ var/      审计数据（pip-freeze 快照等——一次性留痕）
   子路径全部派生（内部布局不做成配置——叶子配置项=把内部结构泄漏成 API，
   每个都是漂移面；QUANT_DATA_ROOT 独立项暂不设——无数据换盘迁移场景，
   设了反造版本对齐问题）。

grype 官方变量 GRYPE_DB_CACHE_DIR 仍向子进程注入（对 grype 是官方接口，
对我们是派生值——见 security_collector._collect_vulns）。
"""
from __future__ import annotations

import os
from pathlib import Path

DEPLOY_ROOT = Path(os.environ.get(
    "QUANT_DEPLOY_ROOT", "/data/websites/snailtrail.cc/quant"))

SHARED = DEPLOY_ROOT / "shared"
VAR = DEPLOY_ROOT / "var"

# shared/ 子路径（运行时与跨版本数据）
SHARED_SECURITY = SHARED / "security"
VENV_LIB = SHARED / "venv" / "lib"
# grype DB 根（注意：GRYPE_DB_CACHE_DIR 名为 cache 实为 DB 根——显式设置时
# grype 直找 <值>/<schema>/，不再自动拼 db/。本目录下应直接是 6/vulnerability.db。
# 2026-10-15 实测：设成上一层会导致 invalid）
GRYPE_DB = SHARED / "grype-db"
STATIC_AVATARS = SHARED / "static" / "avatars"

# 安全校验：DEPLOY_ROOT 指向的树必须含 shared/（错配=快速失败，
# 不等下游各消费方零散报 PermissionError/no such file）
def _sanity() -> None:
    if os.environ.get("QUANT_DEPLOY_ROOT") and not SHARED.exists():
        raise RuntimeError(
            f"QUANT_DEPLOY_ROOT 显式配置但 {SHARED} 不存在——"
            f"检查 .env（缺省内网三环境同路径，通常无需配置）")


_sanity()
