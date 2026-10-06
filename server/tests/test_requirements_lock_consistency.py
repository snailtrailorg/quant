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
_LOCK_PIN = re.compile(r"([A-Za-z0-9][A-Za-z0-9._-]*)(?:\[[^\]]*\])?==")


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
