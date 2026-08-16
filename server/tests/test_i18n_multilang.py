"""N 语言兼容性单测：注册表驱动 + en 缺省回落（2026-08-15 架构约束：加语言=加条目，逻辑零改动）。"""
from src.web_api.terms import get_terms_items, available_langs, LANG_NAMES, TERMS
from src.web_api.email_service import normalize_lang, _render, INVITE_TPL


def test_terms_registry_driven():
    """条款为注册表驱动：items 含全部已实现语言，结构 {lang,name,body}。"""
    items = get_terms_items()
    assert len(items) == len(available_langs()) >= 2
    for it in items:
        assert set(it) == {"lang", "name", "body"}
        assert it["lang"] in LANG_NAMES and it["body"]


def test_normalize_lang_fallback_en():
    """未实现语言回落 en（国际通用缺省）。"""
    assert normalize_lang("zh") == "zh"
    assert normalize_lang("en") == "en"
    assert normalize_lang("fr") == "en"     # 未实现 → en
    assert normalize_lang("") == "en"
    assert normalize_lang(None) == "en"


def test_render_fallback_en():
    """模板按语言取，未知语言回落 en 模板。"""
    s_zh, b_zh = _render(INVITE_TPL, "zh", register_url="https://x/r")
    s_en, b_en = _render(INVITE_TPL, "en", register_url="https://x/r")
    s_fr, b_fr = _render(INVITE_TPL, "fr", register_url="https://x/r")  # 未知 → en
    assert "邀请" in s_zh and "Invited" in s_en
    assert (s_fr, b_fr) == (s_en, b_en)
    assert "https://x/r" in b_zh and "https://x/r" in b_en


def test_new_language_only_needs_entry():
    """模拟新增语言：只在 TERMS/LANG_NAMES/模板加条目，所有遍历逻辑自动生效。"""
    TERMS["xx"] = "XX TERMS"
    LANG_NAMES["xx"] = "XxLang"
    try:
        items = get_terms_items()
        assert any(it["lang"] == "xx" for it in items)
        assert normalize_lang("xx") == "xx"
        # 未加模板的语言邮件回落 en（不崩）
        s, b = _render(INVITE_TPL, "xx", register_url="u")
        assert s == INVITE_TPL["en"]["subject"]
    finally:
        del TERMS["xx"]
        del LANG_NAMES["xx"]
