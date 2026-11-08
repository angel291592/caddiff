"""PPTX 导出的纯逻辑测试（需要 python-pptx，不需要 FreeCAD）。

这里守两条「产物之间不许互相矛盾」的规则：

1. **整件降级时 deck 必须自带 caveat**（issue #23）。HTML / Markdown 已经写明
   「高亮范围＝整个零件」，如果 deck 仍把整件体积印成「减少材料 / 新增材料」，
   三份产物就有两种口径，而 PPT 恰恰是最容易被截图传播的那份。
2. **单侧差异必须能出 deck 且说清缺的是哪一版**（issue #22）。缺失侧不生成图片，
   deck 不能因为少一张图就崩，也不能留下一个空列让人以为「图没生成出来」。
"""
import json

import pytest

pytest.importorskip("pptx")  # python-pptx 是 [pptx] extra；没装时跳过而不是红
from PIL import Image  # noqa: E402  （python-pptx 的依赖，跳过判断之后才安全导入）

import build_pptx  # noqa: E402


def _png(path):
    Image.new("RGB", (120, 90), (255, 255, 255)).save(path)


def _all_text(pptx_path):
    """把整个 deck 的所有文本拼起来——断言落在「读者真的会看到什么」上。"""
    from pptx import Presentation
    prs = Presentation(str(pptx_path))
    chunks = []
    for slide in prs.slides:
        for shape in slide.shapes:
            if shape.has_text_frame:
                chunks.append(shape.text_frame.text)
            if getattr(shape, "has_table", False) and shape.has_table:
                for row in shape.table.rows:
                    for cell in row.cells:
                        chunks.append(cell.text)
    return "\n".join(chunks)


def _picture_count(pptx_path):
    from pptx import Presentation
    from pptx.enum.shapes import MSO_SHAPE_TYPE
    prs = Presentation(str(pptx_path))
    return sum(1 for slide in prs.slides for shape in slide.shapes
               if shape.shape_type == MSO_SHAPE_TYPE.PICTURE)


def _build(tmp_path, entries):
    images = tmp_path / "out" / "images"
    images.mkdir(parents=True, exist_ok=True)
    rm = images / "render_manifest.json"
    rm.write_text(json.dumps(entries), encoding="utf-8")
    out = tmp_path / "out" / "compare.pptx"
    build_pptx.run(str(rm), str(out))
    return out


def test_whole_part_degradation_carries_the_caveat(tmp_path):
    """整件降级：左栏标签与右栏说明都必须点明「整个零件」，不能伪装成精确增减料。"""
    images = tmp_path / "out" / "images"
    images.mkdir(parents=True)
    for name in ("P_0_overview_rect.png", "P_0_closeup_old_rect.png",
                 "P_0_closeup_new_rect.png"):
        _png(images / name)
    entry = {
        "base_name": "P",
        "instance_index": 0,
        "change_type": "shape_changed",
        "degraded_reason": "unpaired_after_proximity",
        "overview_rect": "P_0_overview_rect.png",
        "closeup_old_rect": "P_0_closeup_old_rect.png",
        "closeup_new_rect": "P_0_closeup_new_rect.png",
        "old_volume": 100.0,
        "new_volume": 62.5,
        "volume_delta": 37.5,
        "volume_delta_pct": 37.5,
        "removed_volume": 100.0,
        "added_volume": 62.5,
        "cluster_details": [{"index": 1, "role": "removed", "volume": 100.0,
                             "size_mm": [5.0, 5.0, 2.5]}],
        "diff_bbox_size_mm": [5.0, 5.0, 2.5],
    }
    out = _build(tmp_path, [entry])
    text = _all_text(out)

    assert "Removed (whole part)" in text
    assert "Added (whole part)" in text
    # 右栏说明与 HTML/Markdown 的 report.field.highlight 同一口径
    assert "the highlight is the whole part" in text
    # 图例也要换掉「品红＝改动的材料」否则直接说反
    assert "the whole part (degraded)" in text


def test_one_sided_change_states_the_missing_counterpart(tmp_path):
    """单侧差异：只出一张特写，deck 不崩，并写明另一版没有对应件。"""
    images = tmp_path / "out" / "images"
    images.mkdir(parents=True)
    for name in ("EXTRA_0_overview_rect.png", "EXTRA_0_closeup_new_rect.png"):
        _png(images / name)
    entry = {
        "base_name": "EXTRA",
        "instance_index": 0,
        "change_type": "shape_changed",
        "one_sided": "new_only",
        "degraded_reason": "unpaired_after_proximity",
        "overview_rect": "EXTRA_0_overview_rect.png",
        "closeup_new_rect": "EXTRA_0_closeup_new_rect.png",
        "old_volume": None,
        "new_volume": 62.5,
        "volume_delta": None,
        "volume_delta_pct": None,
        "removed_volume": 0.0,
        "added_volume": 62.5,
        "cluster_details": [{"index": 1, "role": "added", "volume": 62.5,
                             "size_mm": [5.0, 5.0, 2.5]}],
        "diff_bbox_size_mm": [5.0, 5.0, 2.5],
    }
    out = _build(tmp_path, [entry])

    # 缺一张特写不再是崩溃：overview + 新版特写 = 2 张图
    assert out.is_file()
    assert _picture_count(out) == 2

    text = _all_text(out)
    assert "Counterpart" in text
    assert "none in the old revision" in text
    assert "exists only in the new revision" in text
