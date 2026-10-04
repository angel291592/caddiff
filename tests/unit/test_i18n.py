"""i18n 的纯逻辑测试。

i18n 出错的方式很隐蔽：漏一条键不会抛异常，只会在产出物里显示成 `report.title`
这种字面量，或者干脆回退到英文——**必须靠测试兜住**，人工看报告是看不出来的。
"""
import i18n


def setup_function():
    i18n.set_lang("en")


def test_default_language_is_english():
    i18n.set_lang(None)
    assert i18n.get_lang() == "en"
    assert i18n.DEFAULT_LANG == "en"


def test_every_key_exists_in_every_language():
    # 这是本文件最重要的一条：新增文案只补一种语言，在这里立刻红
    for lang, table in i18n._LANGS.items():
        missing = [k for k in i18n._LANGS[i18n.DEFAULT_LANG] if k not in table]
        assert not missing, f"language {lang!r} is missing keys: {missing}"


def test_no_extra_keys_in_non_default_languages():
    # 反向检查：非默认语言里出现默认语言没有的键 = 拼写错误，永远不会被读到
    default_keys = set(i18n._LANGS[i18n.DEFAULT_LANG])
    for lang, table in i18n._LANGS.items():
        extra = [k for k in table if k not in default_keys]
        assert not extra, f"language {lang!r} has unknown keys: {extra}"


def test_switching_language_changes_output():
    i18n.set_lang("en")
    en = i18n.t("label.old")
    i18n.set_lang("zh")
    zh = i18n.t("label.old")
    assert en != zh
    assert en == "Old"


def test_format_placeholders_are_substituted():
    i18n.set_lang("en")
    out = i18n.t("banner.title.overview", tag="-1+1+0")
    assert "-1+1+0" in out
    assert "{" not in out  # 占位符必须被吃干净


def test_unknown_key_returns_key_itself():
    # 故意返回键名：漏翻译会在产出物里显式暴露，而不是静默变成空串
    assert i18n.t("definitely.not.a.key") == "definitely.not.a.key"


def test_missing_placeholder_does_not_raise():
    # 占位符对不上时不能让一次 6 分钟的渲染崩掉，返回未插值原文即可
    out = i18n.t("banner.title.overview")
    assert isinstance(out, str) and out


def test_normalize_lang_accepts_region_variants():
    assert i18n.normalize_lang("zh-CN") == "zh"
    assert i18n.normalize_lang("zh_CN") == "zh"
    assert i18n.normalize_lang("EN-us") == "en"


def test_normalize_lang_falls_back_instead_of_raising():
    # 语言选择不该让任务失败：认不出来就用默认语言
    assert i18n.normalize_lang("klingon") == "en"
    assert i18n.normalize_lang("") == "en"
    assert i18n.normalize_lang(None) == "en"


def test_available_langs_is_sorted_and_contains_default():
    langs = i18n.available_langs()
    assert langs == sorted(langs)
    assert i18n.DEFAULT_LANG in langs
