"""漏洞扫描器注册（批185）。

把「必装件是否存在」收敛到**一个**探测入口，替代散落的 `shutil.which` 判断：
- 调用方只读 `status()`（id / available / reason），不自己 which；
- 缺失时 reason 指明缺哪个二进制——页面 degraded 原因、告警文案直接可用；
- **装位保证在 bootstrap.yml**（系统级 infra 不随 release 工件走，部署断言管不到新机）；
  release 只做 fail-open 探测告警。

新增扫描器 = 加一个 Scanner 子类 + detect_scanner 里纳入候选，调用点零改动。
"""
from __future__ import annotations

import shutil

_REQUIRED = ("grype", "syft")


class Scanner:
    """扫描器能力接口：id / missing() / status()。"""

    id = "none"

    def missing(self) -> list[str]:
        return list(_REQUIRED)

    def status(self) -> dict:
        miss = self.missing()
        return {"id": self.id, "available": not miss,
                "reason": "" if not miss else "缺少二进制: " + ", ".join(miss)}


class GrypeScanner(Scanner):
    """grype + syft 组合（SBOM 生成 + CVE 匹配），批 123 单源终裁。"""

    id = "grype"

    def missing(self) -> list[str]:
        return [b for b in _REQUIRED if not shutil.which(b)]


def detect_scanner() -> Scanner:
    """唯一探测入口（命令存在性优先）。返回实例不代表可用——读 status()。"""
    return GrypeScanner()
