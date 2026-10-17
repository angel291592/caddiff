"""`build_summary` 的纯逻辑测试 —— 这里守着本工具最危险的一类假阴性。

背景（实测过的真实缺陷）：几何比对只处理「两版都存在的同名零件」，纯增件/纯删件
根本进不了几何对比。早先的摘要只数几何差异，于是**「整个装配体被换掉」会呈现为
「0 处差异」**——CI 闸门据此会放行本该拦下的提交。下面的测试就是钉住这个行为。
"""
import json

import i18n
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
    # 无差异的原因必须在契约的 summary 块里（单一入口承诺；决策 D-024）
    assert s["reason"] == i18n.t("summary.no_geometry_diff")


def test_reason_absent_when_there_are_differences(tmp_path):
    # has_differences=True 时不得写 reason："无几何差异"会让 JSON 消费者误读结论
    s = _summary(tmp_path,
                 bom={"removed": ["OLD-A"], "added": [], "candidates": []},
                 manifest=[])
    assert s["has_differences"] is True
    assert s.get("reason") is None


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


# ────────────────────────── --perf 开关语义 ──────────────────────────
# 这一组守的是「性能功能永不改变 CI 闸门的判定」这条对外承诺（AGENTS.md §3.1）。

_PERF_BLOCK = {"delta_volume_mm3": -192.0, "delta_volume_pct": -13.33,
               "centroid_shift_mm": 0.4, "dominant_axis": "\u2212X",
               "contributors": [{"label": "BRACKET-03", "d_mm": [-0.4, 0.0, 0.0]}],
               "old": {"total_volume_mm3": 1440.0, "n_objects": 1, "n_not_counted": 0},
               "new": {"total_volume_mm3": 1248.0, "n_objects": 1, "n_not_counted": 0},
               "settings": {"density_source": "none"}, "unavailable": []}


def _summary_with_perf(tmp_path, perf, **kw):
    return run_pipeline.build_summary(
        _write(tmp_path / "bom.json", {"removed": [], "added": [], "candidates": []}),
        _write(tmp_path / "geom.json", {}),
        _write(tmp_path / "render.json", []),
        perf_json=_write(tmp_path / "perf.json", perf),
        **kw)


def test_performance_block_is_absent_unless_requested(tmp_path):
    # --perf 关闭：summary 里连键都不能出现（无 --perf 的产物必须与旧版一致）
    s = _summary(tmp_path)
    assert "performance_diff" not in s


def test_performance_block_is_carried_when_requested(tmp_path):
    s = _summary_with_perf(tmp_path, _PERF_BLOCK)
    assert s["performance_diff"]["delta_volume_mm3"] == -192.0


def test_missing_performance_artifact_keeps_the_key_but_has_no_numbers(tmp_path):
    # 开了 --perf 但性能产物读不出来：键要在（说明这次开了），数字为空——
    # 不能反过来假装没开，那是把执行失败伪装成「没有性能差异」。
    s = run_pipeline.build_summary(
        _write(tmp_path / "bom.json", {}),
        _write(tmp_path / "geom.json", {}),
        _write(tmp_path / "render.json", []),
        perf_json=str(tmp_path / "does-not-exist.json"))
    assert s["performance_diff"] == {}


def test_performance_never_changes_has_differences(tmp_path):
    # 关 / 开 --perf 两个场景下 has_differences 必须完全一致——它正是退出码 0/1 的唯一判据。
    no_diff = {"removed": [], "added": [], "candidates": [{"base_name": "PLATE",
                                                           "count_match": True}]}
    with_diff = {"removed": ["OLD-A"], "added": [], "candidates": []}
    for bom in (no_diff, with_diff):
        base = run_pipeline.build_summary(
            _write(tmp_path / "bom.json", bom),
            _write(tmp_path / "geom.json", {}),
            _write(tmp_path / "render.json", []))
        with_perf = run_pipeline.build_summary(
            _write(tmp_path / "bom.json", bom),
            _write(tmp_path / "geom.json", {}),
            _write(tmp_path / "render.json", []),
            perf_json=_write(tmp_path / "perf.json", _PERF_BLOCK))
        assert with_perf["has_differences"] == base["has_differences"]


def test_performance_only_run_still_reports_no_differences(tmp_path):
    # 几何/BOM 无差异、只有性能差异：结论仍是「无差异」（退出码 0），
    # 但无差异原因与性能块同时在，报告才有材料解释这两件事为什么能并存。
    s = _summary_with_perf(tmp_path, _PERF_BLOCK)
    assert s["has_differences"] is False
    assert s["reason"] == i18n.t("summary.no_geometry_diff")
    assert s["performance_diff"]["delta_volume_mm3"] == -192.0
