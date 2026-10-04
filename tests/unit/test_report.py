"""报告生成的纯逻辑测试（不依赖 FreeCAD、不联网）。

报告是「给人看的那一半产出物」：manifest 给机器，报告给人。它出错的方式是
**静默少内容**（漏掉跳过件、漏掉图、漏掉 BOM 差异），所以断言必须落在具体内容上。
"""
import json
import os
import re

import i18n
import report

_CJK = re.compile(r"[\u4e00-\u9fff]")


def _build_out(tmp_path, lang="en"):
    """造一个最小但完整的产物目录：manifest + render_manifest + 一张假 PNG。"""
    out = tmp_path / "out"
    images = out / "images"
    images.mkdir(parents=True)

    (images / "BRACKET_0_overview_rect.png").write_bytes(b"\x89PNG\r\n\x1a\n")
    (images / "BRACKET_0_closeup_new_rect.png").write_bytes(b"\x89PNG\r\n\x1a\n")

    render_manifest = [{
        "base_name": "BRACKET",
        "instance_index": 0,
        "change_type": "shape_changed",
        "overview_rect": "BRACKET_0_overview_rect.png",
        "closeup_new_rect": "BRACKET_0_closeup_new_rect.png",
        "volume_delta": -192.0,
        "volume_delta_pct": -15.38,
        "removed_volume": 192.0,
        "added_volume": 0.0,
        "cluster_details": [{"index": 1, "role": "removed", "volume": 192.0,
                             "size_mm": [4.0, 12.0, 4.0]}],
        "diff_bbox_size_mm": [4.0, 12.0, 4.0],
    }]
    rm_path = images / "render_manifest.json"
    rm_path.write_text(json.dumps(render_manifest), encoding="utf-8")

    manifest = {
        "manifest_version": 1,
        "stp_old": "/data/old.stp",
        "stp_new": "/data/new.stp",
        "label_old": "v1",
        "label_new": "v2",
        "lang": lang,
        "images_dir": str(images),
        "render_manifest": str(rm_path),
        "summary": {
            "manifest_version": 1,
            "geometry_diff_count": 1,
            "bom_diff_count": 1,
            "has_differences": True,
            "by_change_type": {"shape_changed": 1},
            "parts_with_diff": [{"base_name": "BRACKET", "change_type": "shape_changed"}],
            "bom_added": ["NEWPART"],
            "bom_removed": ["GONEPART"],
            "bom_count_mismatch": [],
            "skipped_parts": [{"name": "PART-B", "reason": "timeout", "faces": 912}],
            "filtered_by_threshold": [],
            "unresolved_notes": [{"base_name": "PART-D", "note": "no geometry match"}],
            "global_alignment_warning": True,
            "global_alignment": {"checked": True, "misaligned": True,
                                 "median_shift_mm": 12.5, "direction_consistency": 0.95,
                                 "paired_count": 4},
            "settings": {"boolean_timeout_s": 60},
        },
    }
    manifest_path = out / "diff_manifest.json"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")
    return out, manifest_path


def test_generates_both_html_and_markdown(tmp_path):
    out, mp = _build_out(tmp_path)
    html_path, md_path = report.generate(str(mp), lang="en")
    assert os.path.isfile(html_path) and os.path.isfile(md_path)
    assert os.path.basename(html_path) == "report.html"
    assert os.path.basename(md_path) == "report.md"


def test_report_contains_the_changed_part_and_its_images(tmp_path):
    out, mp = _build_out(tmp_path)
    html_path, md_path = report.generate(str(mp), lang="en")
    html_text = open(html_path, encoding="utf-8").read()
    md_text = open(md_path, encoding="utf-8").read()

    for text in (html_text, md_text):
        assert "BRACKET" in text
        assert "BRACKET_0_overview_rect.png" in text
        assert "BRACKET_0_closeup_new_rect.png" in text


def test_report_surfaces_honesty_sections(tmp_path):
    # 跳过件 / 未解析 / 全局配准告警都必须出现在报告里，否则读者会把「没提到」读成「没差异」
    out, mp = _build_out(tmp_path)
    html_path, md_path = report.generate(str(mp), lang="en")
    html_text = open(html_path, encoding="utf-8").read()
    md_text = open(md_path, encoding="utf-8").read()
    for text in (html_text, md_text):
        assert "PART-B" in text          # skipped
        assert "PART-D" in text          # unresolved note
    # BOM 层差异同样要出现
    assert "NEWPART" in md_text and "GONEPART" in md_text


def test_english_output_has_no_cjk(tmp_path):
    # 「英文优先」的机器判据：默认语言下产出物里不得出现任何汉字
    out, mp = _build_out(tmp_path)
    html_path, md_path = report.generate(str(mp), lang="en")
    for p in (html_path, md_path):
        text = open(p, encoding="utf-8").read()
        assert not _CJK.search(text), f"CJK found in {os.path.basename(p)}"


def test_chinese_output_actually_switches(tmp_path):
    out, mp = _build_out(tmp_path)
    html_path, _ = report.generate(str(mp), lang="zh")
    assert _CJK.search(open(html_path, encoding="utf-8").read())
    i18n.set_lang("en")  # 复位，避免污染其它测试


def test_no_differences_case_still_produces_a_report(tmp_path):
    out, mp = _build_out(tmp_path)
    manifest = json.loads(mp.read_text(encoding="utf-8"))
    manifest["summary"].update({"has_differences": False, "geometry_diff_count": 0,
                                "bom_diff_count": 0, "bom_added": [], "bom_removed": [],
                                "skipped_parts": [], "unresolved_notes": [],
                                "global_alignment_warning": False})
    mp.write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")
    # 无差异时渲染清单本来就是空的，这里一并对齐，避免造出一个现实中不会出现的状态
    (out / "images" / "render_manifest.json").write_text("[]", encoding="utf-8")

    html_path, md_path = report.generate(str(mp), lang="en")
    assert os.path.isfile(html_path) and os.path.isfile(md_path)
    md = open(md_path, encoding="utf-8").read()
    # 「没有差异」是有效结论，必须写出来，而不是产出一个空文件
    assert i18n.t("report.none") in md
    assert "Geometric changes" not in md


def test_missing_manifest_raises_runtime_error(tmp_path):
    import pytest
    with pytest.raises(RuntimeError):
        report.generate(str(tmp_path / "nope.json"))
