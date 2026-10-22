"""闸门：`requirements.txt` ↔ `requirements.lock` 一致性（批 104）。

**为什么需要这个闸门（有血证）**：release 的 pip 门是
`deploy/playbooks/release.yml` 的 `req_changed`，它**只比 `requirements.lock` 的指纹**——
`requirements.txt` **不在判据里**；而 `quant-pip-wrapper` 装的也只是 `.lock`
（`--require-hashes -r`）。于是「改了 `.txt`、忘了重生成 `.lock`」时，pip 波打印的是
**`skipping`（不是 `failed`）**⇒ 整条发布链看着全绿，**而 prod 的 venv 里没有这个包**。

实证（2026-10-06，批 102a）：`PySocks>=1.7` 写进了 `.txt` 却没回写 `.lock`
⇒ prod 上 `socks5h://` 代理报 `InvalidSchema: Missing dependencies for SOCKS support.`
（详见 `flow/方案/多市场数据接入与代理体系-设计-20261006.md` §3.8）。**静默跳过型缺陷**，
所以必须用闸门挡，不能靠人记得。

**为什么放在 `tests/` 而不是写个 shell**：收工四件套第 1 项就是全量 pytest ⇒ **零新增
基础设施即被强制**；且跑测试的场景比「记得跑某个脚本」多得多。

**本闸门是单向的**：`txt ⊆ lock`。反向**不查**——lock 必然含大量合法传递依赖
（`aiohttp`、`thriftpy2` 之类），要求 lock ⊆ txt 是错的。

**判据口径**：比的是 PEP 503 规范化后的**项目名**，不比版本（版本由 `pip-compile` 定，
本闸门只保证「没漏包」）。
"""
import re
from pathlib import Path

SERVER_DIR = Path(__file__).resolve().parents[1]
REQ_TXT = SERVER_DIR / "requirements.txt"
REQ_LOCK = SERVER_DIR / "requirements.lock"

_CANON_SEP = re.compile(r"[-_.]+")
_NAME_HEAD = re.compile(r"([A-Za-z0-9][A-Za-z0-9._-]*)")
# ⚠ 必须容忍 extras：`pip-compile` 对带 extras 的声明**原样保留**中括号，
#   写成 `psycopg[binary]==3.3.6`（本仓还有 `fonttools[woff]`、`uvicorn[standard]`）。
#   漏了这个，闸门会对这三种依赖**假报缺失**（本闸门首跑实测抓出的正是 psycopg/uvicorn）。
# 批 125：兼容 file:// 直接引用形态（`vnpy @ file:///...`——fork nogui wheel）
_LOCK_PIN = re.compile(
    r"([A-Za-z0-9][A-Za-z0-9._-]*)(?:\[[^\]]*\])?(?:==| @ )")


def canon(name: str) -> str:
    """PEP 503 规范化：`Foo_Bar.Baz` → `foo-bar-baz`。"""
    return _CANON_SEP.sub("-", name).lower()


def parse_requirements_txt(text: str) -> set[str]:
    """取 `.txt` 里**顶层声明的项目名**集合：丢空行/整行注释/选项行，剥内联注释与 extras。"""
    names: set[str] = set()
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or line.startswith("-"):
            continue          # 空行 / 注释 / `-r`、`--index-url` 等选项行
        # 内联注释（本仓写法 `pkg>=1.0   # 说明`）。要求 `#` 前有空白，以免误伤
        # URL fragment（PEP 508 允许 `pkg @ https://host/#egg=...`）。
        line = re.sub(r"\s+#.*$", "", line).strip()
        if not line:
            continue
        m = _NAME_HEAD.match(line)
        if m:
            names.add(canon(m.group(1)))      # extras（`uvicorn[standard]`）只取名前段
    return names


def parse_lock_names(text: str) -> set[str]:
    """取 `.lock` 里被 pin 的包名（`name==ver` 与 `name[extra]==ver` 行；忽略 `# via`/`--index-url`）。"""
    names: set[str] = set()
    for raw in text.splitlines():
        m = _LOCK_PIN.match(raw)
        if m:
            names.add(canon(m.group(1)))
    return names


def missing_from_lock(txt_text: str, lock_text: str) -> set[str]:
    """在 `.txt` 声明、但 `.lock` 里没有的包名（**应为空集**）。"""
    return parse_requirements_txt(txt_text) - parse_lock_names(lock_text)


# ─────────────────────────── 解析器单测（防闸门自己写歪） ───────────────────────────


class TestTxtParser:
    def test_strips_inline_comment_and_extras(self):
        got = parse_requirements_txt(
            "uvicorn[standard]>=0.29   # Web 服务器\npsycopg[binary]>=3.1\n")
        assert got == {"uvicorn", "psycopg"}

    def test_skips_comments_blanks_and_option_lines(self):
        got = parse_requirements_txt(
            "# 注释\n\n-r other.txt\n--index-url https://x/simple\nPySocks>=1.7\n")
        assert got == {"pysocks"}

    def test_canonicalizes_separators_and_case(self):
        assert parse_requirements_txt("Foo_Bar.Baz>=1\n") == {"foo-bar-baz"}

    def test_bare_name_without_version(self):
        assert parse_requirements_txt("psutil\n") == {"psutil"}

    def test_hash_inside_url_not_treated_as_comment(self):
        # PEP 508 直接引用：`#` 前无空白 ⇒ 不当注释截断（保守：只要名仍能取到即可）
        assert parse_requirements_txt("pkg @ https://host/a#egg=pkg\n") == {"pkg"}


class TestLockParser:
    def test_reads_pinned_names_only(self):
        got = parse_lock_names(
            "--index-url https://x/simple\n"
            "aiohttp==3.14.3 \\\n"
            "    --hash=sha256:aa\n"
            "    # via requests\n")
        assert got == {"aiohttp"}

    def test_ignores_continuation_and_hash_lines(self):
        assert parse_lock_names("    --hash=sha256:bb\n    # via x\n") == set()

    def test_reads_extras_pin(self):
        """pip-compile 对带 extras 的声明保留中括号 ⇒ `psycopg[binary]==3.3.6` 必须能取名。"""
        assert parse_lock_names("psycopg[binary]==3.3.6 \\\n    --hash=sha256:cc\n") == {"psycopg"}
        assert parse_lock_names("uvicorn[standard]==0.54.0\n") == {"uvicorn"}
        assert parse_lock_names("fonttools[woff]==4.65.0\n") == {"fonttools"}


class TestGateIsNotVacuous:
    """闸门本身的自证：合成输入下「缺包必红、在册必绿」——防写成恒真。"""

    def test_flags_missing_dep(self):
        assert missing_from_lock("PySocks>=1.7\n", "requests==2.31\n") == {"pysocks"}

    def test_passes_when_present(self):
        assert missing_from_lock("PySocks>=1.7\n", "pysocks==1.7.1 \\\n    --hash=sha256:x\n") == set()

    def test_passes_when_extras_form(self):
        """两侧都带 extras（声明侧 `uvicorn[standard]>=` / 锁侧 `uvicorn[standard]==`）⇒ 绿。"""
        assert missing_from_lock("uvicorn[standard]>=0.29\n",
                                 "uvicorn[standard]==0.54.0 \\\n    --hash=sha256:y\n") == set()


class TestRealFiles:
    def test_real_files_exist(self):
        assert REQ_TXT.is_file(), f"缺 {REQ_TXT}"
        assert REQ_LOCK.is_file(), f"缺 {REQ_LOCK}"

    def test_every_declared_dep_is_locked(self):
        """**核心断言**：`.txt` 的每个顶层依赖都必须出现在 `.lock` 里。"""
        missing = missing_from_lock(REQ_TXT.read_text(encoding="utf-8"),
                                    REQ_LOCK.read_text(encoding="utf-8"))
        assert not missing, (
            "requirements.txt 里这些顶层依赖**没有**出现在 requirements.lock："
            f"{sorted(missing)}\n"
            "→ 后果：release 的 pip 门只比 .lock 指纹 ⇒ pip 波打印 `skipping`（**不是 failed**）、"
            "全链看着绿，而 prod 的 venv 里没有这个包（批 102a 的 PySocks 即此坑，"
            "表现为 socks5h 代理报 InvalidSchema）。\n"
            "→ 修法：`cd server && venv/bin/pip-compile --generate-hashes "
            "--index-url=https://pypi.tuna.tsinghua.edu.cn/simple "
            "--output-file=requirements.lock requirements.txt`"
            "（复核 diff **只应有新增行**，无删除/无版本漂移）")

    def test_pysocks_still_locked(self):
        """回归钉：**批 102a 的原始事故**——PySocks 必须同时在两侧（这条红了就说明又漏回写了）。"""
        txt = parse_requirements_txt(REQ_TXT.read_text(encoding="utf-8"))
        lock = parse_lock_names(REQ_LOCK.read_text(encoding="utf-8"))
        assert "pysocks" in txt, "requirements.txt 里 PySocks 不见了（socks5h 代理将不可用）"
        assert "pysocks" in lock, "PySocks 在 .txt 但不在 .lock ⇒ prod 必缺（102a 事故重演）"

# ────────────── 批 136 增补：**版本满足**闸门（原闸门只查包名，不查版本） ──────────────
#
# 血证（批 136）：Pillow>=13.0.0 写进 .txt，但 .lock 仍是 pillow==12.3.0 ⇒
# 原闸门（只比包名）全绿，部署按 lock 装 ⇒ prod 实际仍是 12.3，**安全修复假绿**。
# 所以补一条：部署目标 Python（3.11）适用的声明，其版本约束必须被 lock 的 pin 满足。
#（全仓只有 Pillow 带环境标记；标记求值只支持 python_version <op> "x.y" 形态，
# 出现别的标记形态请扩展本闸门，不要静默放过。）

_DEPLOY_PY = (3, 10)   # 部署 venv 的实际解释器版本
# 证据（批 136，2026-10-22）：release 阶段 5 pip 预检拒绝 `pillow==13.0.0`
# （"Requires-Python >=3.11 … require a different python version"）⇒ 部署 venv 是 3.10，
# 与 lock 头 "pip-compile with Python 3.10" 一致。若将来升级 runtime，**同步改这里**。
_SPEC_RE = re.compile(r"^(>=|<=|==|>|<)\s*([0-9][0-9A-Za-z.\-]*)")
_MARKER_RE = re.compile(r'python_version\s*(>=|<=|==|>|<)\s*["\']([0-9.]+)["\']')


def _vtuple(v: str) -> tuple[int, int, int]:
    nums = [int(x) for x in re.findall(r"\d+", v)[:3]]
    while len(nums) < 3:
        nums.append(0)
    return (nums[0], nums[1], nums[2])


def marker_applies(marker: str, py: tuple[int, int] = _DEPLOY_PY) -> bool:
    """部署目标 Python 是否命中该环境标记（无标记=恒真）。"""
    if not marker.strip():
        return True
    m = _MARKER_RE.search(marker)
    if not m:
        raise AssertionError(f"不支持的标记形态：{marker!r}——扩展 marker_applies 后再放行")
    op, ver = m.group(1), _vtuple(m.group(2))[:2]
    return {"<": py < ver, "<=": py <= ver, "==": py == ver,
            ">": py > ver, ">=": py >= ver}[op]


def _satisfies(version: str, op: str, want: str) -> bool:
    a, b = _vtuple(version), _vtuple(want)
    return {"<": a < b, "<=": a <= b, "==": a[:len(b)] == b,
            ">": a > b, ">=": a >= b}[op]


def declared_specs(text: str, py: tuple[int, int] = _DEPLOY_PY) -> dict[str, list[tuple[str, str]]]:
    """部署目标适用的顶层声明：{规范化包名: [(op, 版本), …]}（同名多行=合并约束）。"""
    specs: dict[str, list[tuple[str, str]]] = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or line.startswith("-"):
            continue
        line = re.sub(r"\s+#.*$", "", line).strip()
        if not line:
            continue
        body, _, marker = line.partition(";")
        if not marker_applies(marker, py):
            continue
        m = _NAME_HEAD.match(body.strip())
        if not m:
            continue
        rest = body.strip()[m.end():]
        sm = _SPEC_RE.match(rest.strip())
        if sm:
            specs.setdefault(canon(m.group(1)), []).append((sm.group(1), sm.group(2)))
    return specs


def lock_versions(text: str) -> dict[str, str]:
    """{包名: 版本}（只取 pin 行）。"""
    out: dict[str, str] = {}
    for raw in text.splitlines():
        m = re.match(r"([A-Za-z0-9][A-Za-z0-9._-]*)(?:\[[^\]]*\])?==([0-9][0-9A-Za-z.\-]*)", raw)
        if m:
            out[canon(m.group(1))] = m.group(2)
    return out


def version_violations(txt_text: str, lock_text: str,
                       py: tuple[int, int] = _DEPLOY_PY) -> list[str]:
    """部署目标适用的声明里，lock 的 pin 不满足约束的项（应为空）。"""
    locks = lock_versions(lock_text)
    bad: list[str] = []
    for name, specs in declared_specs(txt_text, py).items():
        ver = locks.get(name)
        if ver is None:
            continue           # 缺包由「包名闸门」负责报
        for op, want in specs:
            if not _satisfies(ver, op, want):
                bad.append(f"{name}: lock {ver} 不满足 {op}{want}（部署目标 py{py[0]}.{py[1]}）")
    return bad


class TestVersionSatisfaction:
    def test_flags_stale_pin(self):
        """判别力：批 136 的真实事故形态（txt>=13 / lock 12.3）必须红。"""
        bad = version_violations('Pillow>=13.0.0; python_version >= "3.11"\n',
                                 "pillow==12.3.0 \\\n    --hash=sha256:x\n", py=(3, 11))
        assert bad and "pillow" in bad[0]

    def test_passes_when_pin_newer(self):
        assert version_violations('PySocks>=1.7\n',
                                  "pysocks==1.7.1 \\\n    --hash=sha256:x\n") == []

    def test_marker_filtered_out(self):
        """仅对 dev(3.10) 适用的声明不参与部署目标判定。"""
        txt = 'Pillow>=99.0.0; python_version < "3.11"\n'
        assert version_violations(txt, "pillow==13.0.0\n", py=(3, 11)) == []

    def test_marker_applied_for_deploy_target(self):
        txt = 'Pillow>=13.0.0; python_version >= "3.11"\n'
        assert version_violations(txt, "pillow==13.0.0\n", py=(3, 11)) == []
        assert version_violations(txt, "pillow==12.3.0\n", py=(3, 11))

    def test_real_files_versions_satisfied(self):
        """核心断言：部署目标下 .txt 的每个版本约束都被 .lock 满足。"""
        bad = version_violations(REQ_TXT.read_text(encoding="utf-8"),
                                 REQ_LOCK.read_text(encoding="utf-8"))
        assert not bad, (
            "requirements.lock 的 pin 不满足 requirements.txt 的版本约束：\n  "
            + "\n  ".join(bad)
            + "\n→ 后果：部署按 lock 装 ⇒ prod 实际版本仍是旧的，安全/功能修复假绿（批 136 的 Pillow 即此坑）。"
        )
