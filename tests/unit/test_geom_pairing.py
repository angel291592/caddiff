r"""`pair_by_proximity` 与孤儿降级路径的纯逻辑测试。

⚠️ 前提：`geom_diff.py` 模块级 `import FreeCAD` / `import Import`，而常驻测试跑在
系统 Python 上（conftest 铁律：不依赖 FreeCAD）。所以这里先向 ``sys.modules``
注入两个空壳 stub 模块再 import 被测函数——测试只需要纯逻辑，不碰 FreeCAD API。

stub 对象属性需求（由函数体确定，不是猜的）：
  obj.Label / obj.Shape；Shape.isNull()=False / .Volume / .BoundBox.Center
  （Center.x/.y/.z + .distanceToPoint()）——pair_by_proximity 全部要什么这里有什么。
"""
import sys
import types
from types import SimpleNamespace

import pytest

# ── FreeCAD / Import stub 注入（必须在 import geom_diff 之前） ──────────────
if "FreeCAD" not in sys.modules:
    sys.modules["FreeCAD"] = types.ModuleType("FreeCAD")
if "Import" not in sys.modules:
    sys.modules["Import"] = types.ModuleType("Import")

import geom_diff  # noqa: E402


class _Center:
    def __init__(self, x, y, z):
        self.x, self.y, self.z = x, y, z

    def distanceToPoint(self, other):
        return ((self.x - other.x) ** 2 + (self.y - other.y) ** 2
                + (self.z - other.z) ** 2) ** 0.5


class _Box:
    """最小 BoundBox 替身：Center + 六端点（get_bbox_dict 读 XMin..ZMax）。"""

    def __init__(self, x, y, z, size=10.0):
        self.Center = _Center(x, y, z)
        self.XMin, self.YMin, self.ZMin = x - size / 2, y - size / 2, z - size / 2
        self.XMax, self.YMax, self.ZMax = x + size / 2, y + size / 2, z + size / 2
        self.XLength = self.YLength = self.ZLength = size


def _part(label, x, y, z, volume):
    shape = SimpleNamespace(isNull=lambda: False, Volume=volume, BoundBox=_Box(x, y, z))
    return SimpleNamespace(Label=label, Shape=shape)


TOL = geom_diff.BBOX_TOLERANCE


def test_normal_pairing_by_distance():
    """同体积候选按中心距离就近配对：近的配近的，不跨距离乱配。"""
    old = [_part("A", 0, 0, 0, 100.0), _part("B", 50, 0, 0, 100.0)]
    new = [_part("B'", 50, 0, 0, 100.0), _part("A'", 0, 0, 0, 100.0)]
    pairs, uo, un, no_shape = geom_diff.pair_by_proximity(old, new)
    assert len(pairs) == 2
    assert {p[0].Label for p in pairs} == {"A", "B"}
    # 就近：A(0,0,0) 必须配 A'(0,0,0)，不是 B'
    for o, n in pairs:
        assert o.Label[0] == n.Label[0]
    assert uo == [] and un == [] and no_shape == []


def test_big_volume_delta_is_filtered_to_symmetric_orphans():
    """体积差 >50% 的对被体积筛拒绝：两侧对称各出一个孤儿，而不是配错或丢一边。"""
    old = [_part("BIG", 0, 0, 0, 341.0)]
    new = [_part("SMALL", 0, 0, 0, 75.0)]  # |341-75|/341 = 78% > 50%
    pairs, uo, un, no_shape = geom_diff.pair_by_proximity(old, new)
    assert pairs == []
    assert [o.Label for o in uo] == ["BIG"]
    assert [o.Label for o in un] == ["SMALL"]
    assert no_shape == []


def test_null_shape_is_returned_not_dropped():
    """无形状对象随返回值带出（现状它静默消失——静默通道的封堵断言）。"""
    null_shape = SimpleNamespace(isNull=lambda: True, Volume=0, BoundBox=None)
    old = [_part("OK", 0, 0, 0, 100.0),
           SimpleNamespace(Label="BROKEN", Shape=null_shape)]
    new = [_part("OK'", 0, 0, 0, 100.0), _part("OK2'", 40, 0, 0, 100.0)]
    pairs, uo, un, no_shape = geom_diff.pair_by_proximity(old, new)
    assert [o.Label for o in no_shape] == ["BROKEN"]  # 不静默：必须带出
    assert len(pairs) == 1 and pairs[0][0].Label == "OK"
    # 旧侧只剩 1 个有效候选、新侧 2 个：多余的新侧进 unpaired_new
    assert [o.Label for o in un] == ["OK2'"]
    assert uo == []


def test_orphan_pairs_produce_degraded_changes():
    """孤儿就近来 1:1 配对并产出降级条目；bbox 永远有值（渲染层直接下标）。"""
    entry = {"base_name": "SOLID", "geometric_changes": [], "note": None}
    changes = entry["geometric_changes"]
    unpaired_old = [_part("SOLID004", 0, 0, 0, 20548.0),
                    _part("SOLID005", 60, 0, 0, 400.0)]
    unpaired_new = [_part("SOLID004'", 0, 0, 0, 9090.0),
                    _part("SOLID005'", 60, 0, 0, 400.0)]
    orphan_pairs = geom_diff._pair_orphans(unpaired_old, unpaired_new)
    assert len(orphan_pairs) == 2
    # 就近：SOLID004(0)↔SOLID004'(0)、SOLID005(60)↔SOLID005'(60)
    assert orphan_pairs[0][0].Label == "SOLID004" and orphan_pairs[0][1].Label == "SOLID004'"
    for oi, (old_o, new_o) in enumerate(orphan_pairs):
        old_shape = geom_diff.get_shape(old_o)
        new_shape = geom_diff.get_shape(new_o)
        changes.append({
            "instance_index": oi,
            "change_type": "shape_changed",
            "old_label": old_o.Label,
            "new_label": new_o.Label,
            "old_volume": old_shape.Volume,
            "new_volume": new_shape.Volume,
            "volume_delta": abs(old_shape.Volume - new_shape.Volume),
            "volume_delta_pct": None,
            "bbox": geom_diff.get_bbox_dict(
                (new_shape if new_shape is not None else old_shape).BoundBox),
            "bbox_old": geom_diff.get_bbox_dict(old_shape.BoundBox),
            "highlight_mode": "whole_part",
            "degraded_reason": "unpaired_after_proximity",
        })
    assert [c["old_label"] for c in changes] == ["SOLID004", "SOLID005"]
    assert changes[0]["volume_delta"] == pytest.approx(abs(20548.0 - 9090.0))
    for c in changes:
        assert c["highlight_mode"] == "whole_part"
        assert c["degraded_reason"] == "unpaired_after_proximity"
        assert c["bbox"]["x_min"] is not None  # bbox 必须实质存在（渲染直接下标）
    # 单侧落单：new 侧没有对象时 old 侧字段照填、对侧置 None
    solo = geom_diff._pair_orphans(unpaired_old[:1], [])
    assert solo == [(unpaired_old[0], None)]


def test_degraded_reason_is_a_known_report_key():
    """新增的降级原因必须能被报告层识别（进 known 集 = 不落 other 兜底）。"""
    assert geom_diff is not None  # 先确保模块已导入（stub 注入后的顺序断言）
    from report import _reason_key
    assert _reason_key("unpaired_after_proximity") == "unpaired_after_proximity"
