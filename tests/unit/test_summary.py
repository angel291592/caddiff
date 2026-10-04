"""`build_summary` 的纯逻辑测试 —— 这里守着本工具最危险的一类假阴性。

背景（实测过的真实缺陷）：几何比对只处理「两版都存在的同名零件」，纯增件/纯删件
根本进不了几何对比。早先的摘要只数几何差异，于是**「整个装配体被换掉」会呈现为
「0 处差异」**——CI 闸门据此会放行本该拦下的提交。下面的测试就是钉住这个行为。
"""
import json

import run_pipeline


def _write(path, obj):
    path.write_text(json.dumps(obj, ensure_ascii=False), encoding="utf-8")
    return str(path)


def _summary(tmp_path, bom=None, geom=None, manifest=None, **kw):
    return run_pipeline.build_summary(
        _write(tmp_path / "bom.json", bom or {}),
        _write(tmp_path / "geom.json", geom or {}),
        _write(tmp_path / "render.json", manifest if manifest is not None else []),
        **kw)


def test_all_parts_replaced_is_reported_as_differences(tmp_path):
    # 最危险的一条：几何层没有任何可比的同名零件，但装配体其实被整体换掉了
    s = _summary(tmp_path,
                 bom={"removed": ["OLD-A", "OLD-B"], "added": ["NEW-A"], "candidates": []},
                 geom={"geometric_diffs": [], "total_candidates": 0},
                 manifest=[])
    assert s["bom_diff_count"] == 3
    assert s["geometry_diff_count"] == 0
    assert s["has_differences"] is True


def test_count_mismatch_alone_counts_as_a_difference(tmp_path):
    s = _summary(tmp_path,
                 bom={"removed": [], "added": [],
                      "candidates": [{"base_name": "BOLT", "count_match": False},
                                     {"base_name": "PLATE", "count_match": True}]})
    assert s["bom_count_mismatch"] == ["BOLT"]
    assert s["has_differences"] is True


def test_no_differences_anywhere(tmp_path):
    s = _summary(tmp_path,
                 bom={"removed": [], "added": [],
                      "candidates": [{"base_name": "PLATE", "count_match": True}]},
                 manifest=[])
    assert s["has_differences"] is False
    assert s["bom_diff_count"] == 0
    assert s["geometry_diff_count"] == 0


def test_geometry_only_difference(tmp_path):
    s = _summary(tmp_path,
                 bom={"removed": [], "added": [], "candidates": []},
                 manifest=[{"base_name": "BRACKET", "change_type": "shape_changed",
                            "volume_delta": -192.0, "volume_delta_pct": -15.38,
                            "removed_volume": 192.0, "added_volume": 0.0,
                            "cluster_count": 1}])
    assert s["geometry_diff_count"] == 1
    assert s["by_change_type"] == {"shape_changed": 1}
    assert s["has_differences"] is True
    assert s["parts_with_diff"][0]["base_name"] == "BRACKET"


def test_moved_entry_carries_translation_not_volumes(tmp_path):
    s = _summary(tmp_path,
                 bom={"removed": [], "added": [], "candidates": []},
                 manifest=[{"base_name": "SLIDER", "change_type": "moved",
                            "translation_mm": 5.0, "rotation_deg": 0.0}])
    item = s["parts_with_diff"][0]
    assert item["translation_mm"] == 5.0
    assert "volume_delta" not in item  # moved 没有对称差，不该凭空造出体积数字


def test_honesty_fields_are_projected_from_geom(tmp_path):
    # 「没提到」绝不能被读成「没差异」：跳过 / 筛掉 / 未解析三类必须原样带出
    s = _summary(tmp_path,
                 bom={"removed": [], "added": [], "candidates": []},
                 geom={"skipped_parts": [{"name": "PART-B", "instance_index": 0,
                                          "reason": "timeout", "faces": 912}],
                       "filtered_by_threshold": [{"name": "PART-C", "volume_delta": 0.4,
                                                  "volume_delta_pct": 0.02,
                                                  "threshold_pct": 0.1}],
                       "geometric_diffs": [{"base_name": "PART-D",
                                            "note": "no geometry match"}],
                       "global_alignment": {"checked": True, "misaligned": True,
                                            "median_shift_mm": 12.5,
                                            "direction_consistency": 0.95}})
    assert s["skipped_parts"][0]["reason"] == "timeout"
    assert s["filtered_by_threshold"][0]["name"] == "PART-C"
    assert s["unresolved_notes"] == [{"base_name": "PART-D", "note": "no geometry match"}]
    assert s["global_alignment_warning"] is True


def test_missing_upstream_files_do_not_raise(tmp_path):
    # 上游产物缺失时摘要仍要能构造出来（诚实字段为空 + has_differences=False），
    # 不能在这里抛异常——真正的失败判定由各步骤自己的退出码负责。
    s = run_pipeline.build_summary(
        str(tmp_path / "nope1.json"), str(tmp_path / "nope2.json"),
        str(tmp_path / "nope3.json"))
    assert s["has_differences"] is False
    assert s["manifest_version"] == run_pipeline.MANIFEST_VERSION


def test_labels_and_settings_are_passed_through(tmp_path):
    s = _summary(tmp_path, label_old="v1.2", label_new="v1.3",
                 geom={"settings": {"boolean_timeout_s": 60, "max_faces_for_boolean": 5000}})
    assert s["label_old"] == "v1.2"
    assert s["label_new"] == "v1.3"
    assert s["settings"]["boolean_timeout_s"] == 60
