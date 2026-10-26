"""批 121 · A06 §11 输入→消费者地图一致性钉（解析 markdown 表→逐行断言）。

## 为什么需要这条闸门（批 121 立法对象）

部署链每个输入（切片/指纹/lock/wrapper/sudoers/nginx conf…）的消费者散在 15 个
文件的注释里——改输入的人无法廉价知道「改这个会断谁」，只能断一次、修一次、补一个
检查（批 121 立项根因：三断环同根因）。地图本体落 A06 §11（人查），**漂移由本测试守**
（机查）：表里每行每条 `file:line` 锚都要真实存在——文件被删/行号漂走/内容换位，
本测试即红，地图永不假绿。

## 钉三段

1. **表活着**：A06 §11 章节存在、表头 schema 五列齐全、数据行 ≥ 15（六类输入全覆盖
   是批 121 验收 1 的判据，行数下限防地图被掏空）。
2. **锚真实（弱断言·逐行全量）**：消费者列每条 `path:line` —— path 相对仓库根存在、
   且该行非空。地图任何一行漂移即红。
3. **锚有据（强断言·抽样）**：抽核行做「该行±50 行窗口内含锚词」——防「行存在但内容
   已换位」的假锚（弱断言盲区）。

**覆盖边界（诚实声明）**：本测试守「表与代码不漂移」；表内容本身的完备性（该进的
输入没进表）靠评审与 §9 炸半径清单交叉，不在断言范围。
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[2]
_A06 = _REPO / "docs" / "architecture" / "A06-部署管道架构.md"

# 抽核行（强断言样本）: (输入名片段, 消费者 path 尾段, 行锚, 该行±50 行窗口必含词)
_STRONG_SPOT_CHECKS = [
    ("requirements.lock", "quant-pip-wrapper", 75, "requirements.lock"),
    ("requirements-dev", "quant-pip-wrapper", 103, "requirements-dev"),   # 批 151 校准（rebuild 分支加入后行移）
    ("deploy_slices", "release.yml", 162, "deploy_slices"),
    ("sudoers", "bootstrap.yml", 41, "sudoers-quant-deploy.j2"),
    ("deploy_hub_md_providers", "release.yml", 243, "deploy_hub_md_providers"),
    ("schema_expectations", "test_deploy_blast_radius.py", 69, "schema_expectations"),
]


def _read_a06() -> str:
    return _A06.read_text(encoding="utf-8")


def _extract_section(text: str) -> str:
    m = re.search(r"^## 11\..*?(?=^## \d|\Z)", text, re.M | re.S)
    assert m, "A06 缺 §11 输入→消费者地图章节"
    return m.group(0)


def _table_rows(section: str) -> list[list[str]]:
    rows: list[list[str]] = []
    for line in section.splitlines():
        if not line.startswith("|"):
            continue
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cells) != 5:
            continue
        if all(set(c) <= {"-", " ", ":"} for c in cells):  # 分隔行
            continue
        rows.append(cells)
    return rows


# 表内短名 → 仓库相对路径（表格首处全名引用后允许短名，这里补齐解析）
_PATH_HINTS = {
    "release.yml": "deploy/playbooks/release.yml",
    "bootstrap.yml": "deploy/playbooks/bootstrap.yml",
    "bootstrap_server.sh": "deploy/scripts/bootstrap_server.sh",
    "rollback-tasks.yml": "deploy/playbooks/rollback-tasks.yml",
    "wave.yml": "deploy/playbooks/wave.yml",
}


def _resolve(path: str) -> Path:
    if (_REPO / path).is_file():
        return _REPO / path
    hinted = _PATH_HINTS.get(path)
    if hinted and (_REPO / hinted).is_file():
        return _REPO / hinted
    # 无扩展名 wrapper（quant-*）短名 → 仓库 wrappers/ 目录
    if "/" not in path and path.startswith("quant-"):
        cand = _REPO / "deploy" / "wrappers" / path
        if cand.is_file():
            return cand
    return _REPO / path  # 原样返回，让断言报「不存在」


def _consumer_refs(cell: str) -> list[tuple[str, int]]:
    """消费者列 → [(path, line)]；兼容 `xxx/yyy.yml:123`、无扩展名 wrapper（quant-*）与反引号包裹。"""
    refs = []
    for m in re.finditer(
        r"`?([\w./-]*quant-[\w-]*|[\w./-]+\.(?:yml|j2|py|sh|txt|conf|lock)):(\d+)`?", cell
    ):
        refs.append((m.group(1), int(m.group(2))))
    return refs


class TestMapAlive:
    def test_section_and_schema(self):
        sec = _extract_section(_read_a06())
        header = sec.splitlines()
        heads = [h for h in header if h.startswith("|") and "输入" in h]
        assert heads, "§11 缺表头"
        for col in ("输入", "属主文件", "消费者", "改动前必查", "关联闸门"):
            assert col in heads[0], f"§11 表头缺列: {col}"

    def test_min_rows(self):
        rows = _table_rows(_extract_section(_read_a06()))
        # 首行是表头
        data = [r for r in rows if not r[0].startswith("输入")]
        assert len(data) >= 15, f"§11 数据行 {len(data)} < 15（六类输入覆盖不足）"

    def test_six_input_classes_covered(self):
        """六类输入（批 121 验收 1）：切片/指纹(lock 与 dev)/wrapper/sudoers/nginx conf。"""
        sec = _extract_section(_read_a06())
        for kw in ("deploy_slices", "requirements.lock", "requirements-dev",
                   "wrapper", "sudoers", "nginx"):
            assert kw in sec, f"§11 缺输入类: {kw}"


class TestAnchorsExist:
    """弱断言全量：每条 file:line —— 文件在 + 该行非空。"""

    def test_all_consumer_anchors(self):
        rows = _table_rows(_extract_section(_read_a06()))
        data = [r for r in rows if not r[0].startswith("输入")]
        n_refs = 0
        for row in data:
            for path, line in _consumer_refs(row[2]):
                p = _resolve(path)
                assert p.is_file(), f"[{row[0]}] 消费者文件不存在: {path}"
                lines = p.read_text(encoding="utf-8").splitlines()
                assert 1 <= line <= len(lines), (
                    f"[{row[0]}] 行号越界: {path}:{line}（文件共 {len(lines)} 行）"
                )
                assert lines[line - 1].strip(), f"[{row[0]}] 空行锚: {path}:{line}"
                n_refs += 1
        assert n_refs >= 20, f"锚总数 {n_refs} < 20（消费者列解析失败?）"


class TestAnchorsSubstantive:
    """强断言抽样：锚行 ±50 行窗口内含锚词（防行号漂到无关内容）。"""

    @pytest.mark.parametrize(
        "input_kw,path_tail,line,needle",
        _STRONG_SPOT_CHECKS,
        ids=[s[0] for s in _STRONG_SPOT_CHECKS],
    )
    def test_spot_anchor_window(self, input_kw, path_tail, line, needle):
        rows = _table_rows(_extract_section(_read_a06()))
        matched = [r for r in rows if input_kw in r[0] and path_tail in r[2]]
        assert matched, f"§11 找不到行: 输入含 {input_kw} 且消费者含 {path_tail}"
        row = matched[0]
        refs = [(rp, ln) for rp, ln in _consumer_refs(row[2]) if rp.endswith(path_tail) and ln == line]
        assert refs, f"[{row[0]}] 消费者列缺 {path_tail}:{line} 精确锚"
        p = _resolve(refs[0][0])
        lines = p.read_text(encoding="utf-8").splitlines()
        lo, hi = max(0, line - 1 - 50), min(len(lines), line - 1 + 51)
        window = "\n".join(lines[lo:hi])
        assert needle in window, (
            f"[{row[0]}] {path_tail}:{line} ±50 行窗口未见锚词 {needle!r}（锚已漂移）"
        )
