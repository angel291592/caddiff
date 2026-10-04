"""BOM 层对齐的纯逻辑测试（不依赖 FreeCAD）。

BOM 对齐是整个工具的第一道关：它决定「哪些零件要拿去比几何」。它错了，后面全是伪差异。
"""
import bom_diff


def _write_stp(path, product_names):
    """造一个最小可解析的 STEP 文本：只含 PRODUCT 记录，够 bom_diff 的正则用。"""
    lines = ["ISO-10303-21;", "HEADER;", "ENDSEC;", "DATA;"]
    for i, name in enumerate(product_names):
        lines.append(f"#{i + 1}=PRODUCT('{name}','{name}','',(#99));")
    lines.append("ENDSEC;")
    lines.append("END-ISO-10303-21;")
    path.write_text("\n".join(lines), encoding="utf-8")
    return str(path)


def test_base_name_strips_trailing_instance_suffix():
    # Creo 每次导出会在零件名末尾追加实例编号段，跨版本比对必须先归并
    assert bom_diff.base_name("BRACKET-01") == "BRACKET"
    assert bom_diff.base_name("BASEPLATE_01") == "BASEPLATE"
    assert bom_diff.base_name("SLIDER_02") == "SLIDER"


def test_base_name_is_idempotent_on_plain_names():
    # 没有实例编号时不得削掉名字本身——这是归并规则的边界
    assert bom_diff.base_name("BRACKET") == "BRACKET"
    assert bom_diff.base_name("PART-A") == "PART-A"


def test_extract_product_names_reads_all_records(tmp_path):
    p = _write_stp(tmp_path / "a.stp", ["PLATE_01", "BOLT_02", "BOLT_03"])
    assert bom_diff.extract_product_names(p) == ["PLATE_01", "BOLT_02", "BOLT_03"]


def test_diff_bom_detects_added_removed_and_count_change(tmp_path):
    old = _write_stp(tmp_path / "old.stp", ["PLATE_01", "BOLT_02", "BOLT_03", "GONE_04"])
    new = _write_stp(tmp_path / "new.stp", ["PLATE_01", "BOLT_02", "BOLT_03",
                                            "BOLT_04", "NEWPART_05"])
    res = bom_diff.diff_bom(old, new)

    # 纯删件：只在旧版出现
    assert res["removed"] == ["GONE"]
    # 纯增件：只在新版出现
    assert res["added"] == ["NEWPART"]
    # 同名零件数量从 2 变 3 —— 这本身就是差异，必须能被下游看见
    mismatch = [c["base_name"] for c in res["candidates"] if not c["count_match"]]
    assert mismatch == ["BOLT"]
    # PLATE 数量未变，不应出现在 mismatch 里
    assert [c["base_name"] for c in res["candidates"] if c["count_match"]] == ["PLATE"]


def test_diff_bom_identical_input_has_no_differences(tmp_path):
    names = ["PLATE_01", "BOLT_02"]
    old = _write_stp(tmp_path / "old.stp", names)
    new = _write_stp(tmp_path / "new.stp", names)
    res = bom_diff.diff_bom(old, new)
    assert res["added"] == []
    assert res["removed"] == []
    assert all(c["count_match"] for c in res["candidates"])


def test_diff_bom_handles_file_without_products(tmp_path):
    # 空装配体不应抛异常——它是合法输入，结论是「没有可比零件」
    empty = _write_stp(tmp_path / "empty.stp", [])
    old = _write_stp(tmp_path / "old.stp", ["PLATE_01"])
    res = bom_diff.diff_bom(old, empty)
    assert res["removed"] == ["PLATE"]
    assert res["candidates"] == []
