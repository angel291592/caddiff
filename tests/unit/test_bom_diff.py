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
    # key 用精确名而不是族名：该族两版名字集合相同 -> 不折叠（见 align_keys）
    assert [c["base_name"] for c in res["candidates"] if c["count_match"]] == ["PLATE_01"]


def test_diff_bom_keeps_distinct_parts_apart_when_names_are_stable(tmp_path):
    """同前缀的不同零件在两版名字稳定时不得被并成一个候选。

    这是 CI 闸门假阴性的复现用例：真实公开装配体（openDogV3）的 8 个实体零件名形如
    ``openDog V3_internals_toleranced v001``..``v11``。旧规则把它们全并成同一个
    base_name，几何层的容器判定于是**自我命中**，整组连同容器一起被跳过——1 处真实的
    5mm 位移被报成「无差异」、退出码 0。两版名字稳定时必须逐个保留为独立候选。
    """
    names = ["openDog V3_internals_toleranced v001",
             "openDog V3_internals_toleranced v002",
             "openDog V3_internals_toleranced v11"]
    old = _write_stp(tmp_path / "old.stp", names)
    new = _write_stp(tmp_path / "new.stp", names)
    res = bom_diff.diff_bom(old, new)

    assert [c["base_name"] for c in res["candidates"]] == sorted(names)
    assert res["added"] == [] and res["removed"] == []


def test_diff_bom_folds_when_names_drift_between_versions(tmp_path):
    """实例编号跨版本漂移时仍必须折叠——这是 base_name 存在的理由，不能被上一条改掉。"""
    old = _write_stp(tmp_path / "old.stp", ["BRACKET-01", "BRACKET-02"])
    new = _write_stp(tmp_path / "new.stp", ["BRACKET-07", "BRACKET-08"])
    res = bom_diff.diff_bom(old, new)

    assert [c["base_name"] for c in res["candidates"]] == ["BRACKET"]
    assert res["added"] == [] and res["removed"] == []


def test_diff_bom_exposes_key_map_for_the_geometry_layer(tmp_path):
    """key_map 是几何层复用同一套 key 的载体（单一真相源）；两层各算一遍必然漂移。"""
    old = _write_stp(tmp_path / "old.stp", ["PLATE_01"])
    new = _write_stp(tmp_path / "new.stp", ["PLATE_01"])
    res = bom_diff.diff_bom(old, new)

    assert res["old_key_map"] == {"PLATE_01": "PLATE_01"}
    assert res["new_key_map"] == {"PLATE_01": "PLATE_01"}


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
