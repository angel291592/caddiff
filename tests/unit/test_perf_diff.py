"""性能层的纯逻辑测试（不依赖 FreeCAD、不联网、导入时零副作用）。

为什么这些断言值得常驻：性能数字是「看起来很可信」的一类产出物——算错了不会抛异常，
只会给出一串看上去正常的数字。最危险的两个具体形态：
① 装配容器与它的子件被各算一遍（体积正好翻倍，坑 F1 实测比值 2.0）；
② 质心位移的分摊不满足恒等式，于是「谁是主犯」是编出来的。
"""
import math

import pytest

import perf_diff
import perf_snapshot


# ────────────────────────── 造快照（纯字典，不碰 FreeCAD） ──────────────────────────

def _obj(label, volume, centroid, tensor=None):
    return {
        "label": label,
        "base_name": label.split("-")[0],
        "volume_mm3": volume,
        "area_mm2": 10.0,
        "bbox_mm": [5.0, 5.0, 5.0],
        "centroid_mm": list(centroid),
        "centroid_source": "CenterOfMass",
        "tensor_6": tensor,
    }


def _snapshot(objects, bbox=(100.0, 40.0, 20.0), inertia=None, principal=None,
              unavailable=None, zero=0, negative=0):
    total = sum(o["volume_mm3"] for o in objects)
    if total > 0.0:
        centroid = [sum(o["volume_mm3"] * o["centroid_mm"][i] for o in objects) / total
                    for i in range(3)]
    else:
        centroid = [0.0, 0.0, 0.0]
    return {
        "side": "old",
        "n_objects": len(objects),
        "n_zero_volume": zero,
        "n_negative_volume": negative,
        "total_volume_mm3": total,
        "total_area_mm2": 0.0,
        "centroid_mm": centroid,
        "inertia_about_centroid_6": inertia,
        "principal_moments": principal,
        "principal_axes": None,
        "bbox_mm": list(bbox),
        "objects": objects,
        "unavailable": unavailable or [],
        "elapsed_s": 0.01,
    }


def _sum_contributors(block):
    total = [0.0, 0.0, 0.0]
    for item in block["contributors"]:
        for i in range(3):
            total[i] += item["d_mm"][i]
    return total


# ────────────────────────── 分摊恒等式（含增删件） ──────────────────────────

def test_allocation_identity_holds_with_added_and_removed_parts():
    # old: A + B ; new: A'（自身也动了）+ C（B 被删、C 被增）——分摊必须把增删件与
    # 仍在的原件的位移一起吸收，否则 Σd_i ≠ ΔC，读者看到的「主犯」就是凭空捏造的。
    old = _snapshot([_obj("A-01", 100.0, (0.0, 0.0, 0.0)),
                     _obj("B-02", 50.0, (10.0, 0.0, 0.0))])
    new = _snapshot([_obj("A-01", 100.0, (1.0, 0.0, 0.0)),
                     _obj("C-03", 80.0, (-5.0, 0.0, 0.0))])
    block = perf_diff.diff(old, new)
    shift = block["centroid_shift_components"]
    assert len(block["contributors"]) == 3            # A / B / C 三族都在并集里
    for i in range(3):
        assert abs(_sum_contributors(block)[i] - shift[i]) < 1e-9
    assert block["degraded_reason"] is None
    assert block["dominant_axis"] == "\u2212X"        # 新版整体往 −X 偏


def test_one_sided_part_contributes_its_full_first_moment():
    # 只存在于旧版的零件：均衡分摊式里它是「消失了的第一矩」，必须能被单独看见
    old = _snapshot([_obj("GONE-01", 200.0, (30.0, 0.0, 0.0))])
    new = _snapshot([])
    block = perf_diff.diff(old, new)
    labels = {item["label"] for item in block["contributors"]}
    assert labels == {"GONE-01"}
    assert abs(block["centroid_shift_components"][0] - (-30.0)) < 1e-9
    assert abs(_sum_contributors(block)[0] - (-30.0)) < 1e-9


# ────────────────────────── 容差与「无变化」判定 ──────────────────────────

def test_noise_level_shift_is_reported_as_unchanged():
    old = _snapshot([_obj("A-01", 100.0, (0.0, 0.0, 0.0))])
    new = _snapshot([_obj("A-01", 100.0, (1e-15, 0.0, 0.0))])
    block = perf_diff.diff(old, new)
    assert block["dominant_axis"] is None
    assert block["contributors"] == []
    assert block["centroid_shift_mm"] > 0.0            # 数值照实给，只是不宣称有位移


def test_relative_tolerance_is_scaled_by_assembly_size():
    # 同一个绝对位移，在小装配体上算「变了」，在大装配体上算「噪声」——判据是相对的
    small_old = _snapshot([_obj("A-01", 100.0, (0.0, 0.0, 0.0))], bbox=(1.0, 1.0, 1.0))
    small_new = _snapshot([_obj("A-01", 100.0, (1e-4, 0.0, 0.0))], bbox=(1.0, 1.0, 1.0))
    big_old = _snapshot([_obj("A-01", 100.0, (0.0, 0.0, 0.0))], bbox=(1000.0, 1000.0, 1000.0))
    big_new = _snapshot([_obj("A-01", 100.0, (1e-4, 0.0, 0.0))], bbox=(1000.0, 1000.0, 1000.0))
    assert perf_diff.diff(small_old, small_new)["dominant_axis"] == "+X"
    assert perf_diff.diff(big_old, big_new)["dominant_axis"] is None


def test_delta_volume_percent_is_none_when_the_old_baseline_is_zero():
    old = _snapshot([])
    new = _snapshot([_obj("NEW-01", 100.0, (0.0, 0.0, 0.0))])
    block = perf_diff.diff(old, new)
    assert block["delta_volume_mm3"] == 100.0
    assert block["delta_volume_pct"] is None           # 近 0 基准：不写 0、也不写 ∞


# ────────────────────────── 非法入参必须被拒绝 ──────────────────────────

@pytest.mark.parametrize("bad", [float("nan"), float("inf"), float("-inf")])
def test_non_finite_volume_is_rejected(bad):
    old = _snapshot([_obj("A-01", 100.0, (0.0, 0.0, 0.0))])
    old["total_volume_mm3"] = bad
    with pytest.raises(ValueError):
        perf_diff.diff(old, _snapshot([_obj("A-01", 100.0, (0.0, 0.0, 0.0))]))


def test_non_finite_centroid_is_rejected():
    new = _snapshot([_obj("A-01", 100.0, (0.0, 0.0, 0.0))])
    new["centroid_mm"] = [0.0, float("inf"), 0.0]
    with pytest.raises(ValueError):
        perf_diff.diff(_snapshot([_obj("A-01", 100.0, (0.0, 0.0, 0.0))]), new)


def test_malformed_snapshot_is_rejected_not_silently_zeroed():
    with pytest.raises(ValueError):
        perf_diff.diff({}, _snapshot([_obj("A-01", 100.0, (0.0, 0.0, 0.0))]))


# ────────────────────────── 张量有效性（物理判据） ──────────────────────────

def test_principal_moments_triangle_violation_is_unavailable():
    # 三主矩 (10, 1, 1) 违反 λ_max ≤ λ_mid + λ_min：这不是"零件很薄"，是张量已经坏了。
    # 判据必须报不可用，绝不取绝对值掩盖。
    moments, axes = perf_snapshot.principal_from_tensor([10.0, 1.0, 1.0, 0.0, 0.0, 0.0])
    assert moments is None and axes is None
    assert not perf_snapshot.principal_valid([10.0, 1.0, 1.0])


def test_valid_tensor_returns_descending_principal_moments_and_axes():
    moments, axes = perf_snapshot.principal_from_tensor([10.0, 6.0, 5.0, 0.0, 0.0, 0.0])
    assert moments is not None
    assert moments == sorted(moments, reverse=True)
    assert abs(moments[0] - 10.0) < 1e-9
    assert abs(moments[2] - 5.0) < 1e-9
    assert len(axes) == 3 and all(len(axis) == 3 for axis in axes)


def test_thin_rod_like_tensor_is_still_valid():
    # 细长杆的真实主矩就是 (大, 小, 小) 且取等号：相对容差必须放它过去，
    # 严格小于会把合法零件判成坏张量（plan §3.5 的解析反例）。
    assert perf_snapshot.principal_valid([100.0, 50.0, 50.0])
    assert perf_snapshot.principal_valid([100.0, 60.0, 40.0])


def test_tensor_validity_slack_is_relative_not_absolute():
    # 这条钉住那个相对容差**真的在起作用**（它不是装饰参数）：超出等号的极小量在默认容差下
    # 放过，容差归零时被拒。容差是相对 λ_max 的，与 --perf-tol（两版比较容差）是两件事。
    assert perf_snapshot.principal_valid([100.0 + 1e-8, 50.0, 50.0])
    assert not perf_snapshot.principal_valid([100.0 + 1e-8, 50.0, 50.0], rel_tol=0.0)


def test_non_finite_tensor_reports_the_side_that_actually_broke():
    # 把「新版是 NaN」写成「旧版缺张量」等于给排查指错方向：unavailable 的 side 必须是真的那一侧。
    old = _snapshot([_obj("A-01", 100.0, (0.0, 0.0, 0.0))],
                    inertia=[1.0, 1.0, 1.0, 0.0, 0.0, 0.0], principal=[3.0, 2.0, 1.0])
    new = _snapshot([_obj("A-01", 100.0, (0.0, 0.0, 0.0))],
                    inertia=[1.0, 1.0, float("nan"), 0.0, 0.0, 0.0], principal=[3.0, 2.0, 1.0])
    block = perf_diff.diff(old, new)
    assert block["delta_inertia_6"] is None
    sides = [item["side"] for item in block["unavailable"]
             if item["reason"] == perf_diff.REASON_NO_INERTIA_TENSOR]
    assert sides == ["new"]


def test_missing_tensor_is_reported_as_unavailable_not_as_zero():
    old = _snapshot([_obj("A-01", 100.0, (0.0, 0.0, 0.0))])
    new = _snapshot([_obj("A-01", 100.0, (0.0, 0.0, 0.0))],
                    inertia=[1.0, 1.0, 1.0, 0.0, 0.0, 0.0], principal=[2.0, 2.0, 1.0])
    block = perf_diff.diff(old, new)
    assert block["delta_inertia_6"] is None
    assert block["delta_principal_moments"] is None
    reasons = {item["reason"] for item in block["unavailable"]}
    assert perf_diff.REASON_NO_INERTIA_TENSOR in reasons


def test_deltas_keep_their_own_lengths_six_and_three():
    # 张量是 6 个分量、主矩是 3 个——把两者当成同一种长度会让主矩差永远是 None
    # （E2E 实测踩到过：主矩差静默变成 null，报告里那一行随之消失）。
    inertia = [1.0, 2.0, 3.0, 0.1, 0.2, 0.3]
    moments = [9.0, 8.0, 7.0]
    old = _snapshot([_obj("A-01", 100.0, (0.0, 0.0, 0.0))],
                    inertia=inertia, principal=moments)
    new = _snapshot([_obj("A-01", 200.0, (1.0, 0.0, 0.0))],
                    inertia=[2.0, 4.0, 6.0, 0.2, 0.4, 0.6], principal=[19.0, 18.0, 17.0])
    block = perf_diff.diff(old, new)
    assert len(block["delta_inertia_6"]) == 6
    assert abs(block["delta_inertia_6"][0] - 1.0) < 1e-12
    assert len(block["delta_principal_moments"]) == 3
    assert abs(block["delta_principal_moments"][0] - 10.0) < 1e-12
    assert block["unavailable"] == []


# ────────────────────────── 空快照与质量 ──────────────────────────

def test_empty_snapshots_do_not_raise():
    block = perf_diff.diff(_snapshot([]), _snapshot([]))
    assert block["delta_volume_mm3"] == 0.0
    assert block["delta_volume_pct"] is None
    assert block["contributors"] == []
    assert block["delta_inertia_6"] is None


def test_no_mass_without_an_explicit_density():
    old = _snapshot([_obj("A-01", 100.0, (0.0, 0.0, 0.0))])
    new = _snapshot([_obj("A-01", 200.0, (0.0, 0.0, 0.0))])
    block = perf_diff.diff(old, new)
    assert block["mass_kg_old"] is None and block["mass_kg_new"] is None
    assert block["delta_mass_kg"] is None
    assert block["settings"]["density_source"] == "none"


def test_mass_uses_the_confirmed_density_only():
    old = _snapshot([_obj("A-01", 1000.0, (0.0, 0.0, 0.0))])
    new = _snapshot([_obj("A-01", 2000.0, (0.0, 0.0, 0.0))])
    block = perf_diff.diff(old, new, density_kg_per_mm3=7.85e-6)
    assert abs(block["mass_kg_old"] - 1000.0 * 7.85e-6) < 1e-12
    assert abs(block["mass_kg_new"] - 2000.0 * 7.85e-6) < 1e-12
    assert block["settings"]["density_source"] == "cli"


# ────────────────────────── --density 的取值解析 ──────────────────────────

def test_density_units_are_converted_to_kg_per_mm3():
    assert abs(perf_diff.parse_density("7.85 g/cm3") - 7.85e-6) < 1e-15
    assert abs(perf_diff.parse_density("7850 kg/m3") - 7.85e-6) < 1e-15
    assert abs(perf_diff.parse_density("7.85e-06 kg/mm3") - 7.85e-6) < 1e-18
    assert abs(perf_diff.parse_density("7.85g/cm\u00b3") - 7.85e-6) < 1e-15
    assert abs(perf_diff.parse_density("0.00000785") - 7.85e-6) < 1e-18   # 无单位 = kg/mm³


def test_unknown_density_unit_is_rejected():
    # 写错单位（例如把 g/cm3 与 kg/mm3 混了）会差 1e6 倍：必须报错，不能猜。
    for spec in ("7.85 g/l", "abc", "7.85 g/cm3 junk", "-1 g/cm3", "0 g/cm3"):
        with pytest.raises(ValueError):
            perf_diff.parse_density(spec)
    # 不写单位按 kg/mm³，是约定不是错误（FreeCAD 的内部单位）
    assert abs(perf_diff.parse_density("7.85") - 7.85) < 1e-15


# ────────────────────────── 遍历规则与聚合（假对象，仍不碰 FreeCAD） ──────────────────────────

class _FakeBBox:
    def __init__(self, x, y, z):
        self.XMin, self.YMin, self.ZMin = 0.0, 0.0, 0.0
        self.XMax, self.YMax, self.ZMax = x, y, z

    @property
    def XLength(self):
        return self.XMax - self.XMin

    @property
    def YLength(self):
        return self.YMax - self.YMin

    @property
    def ZLength(self):
        return self.ZMax - self.ZMin


class _FakeVector:
    def __init__(self, x, y, z):
        self.x, self.y, self.z = x, y, z


class _FakeMatrix:
    def __init__(self, t6):
        self.A11, self.A22, self.A33, self.A12, self.A13, self.A23 = t6


class _FakeShape:
    def __init__(self, volume, centroid=(0.0, 0.0, 0.0), tensor=(0.0, 0.0, 0.0, 0.0, 0.0, 0.0),
                 n_solids=1, size=(1.0, 1.0, 1.0)):
        self.Volume = volume
        self.Area = 6.0
        self.Solids = [object()] * n_solids
        self.BoundBox = _FakeBBox(*size)
        self.MatrixOfInertia = _FakeMatrix(tensor)
        self._centroid = centroid

    @property
    def CenterOfMass(self):
        return _FakeVector(*self._centroid)


class _FakeObj:
    def __init__(self, label, shape=None, group=None, type_id="Part::Feature"):
        self.Label = label
        self.Name = label
        self.TypeId = type_id
        if shape is not None:
            self.Shape = shape
        if group is not None:
            self.Group = group


def test_container_and_children_are_not_double_counted_and_leaves_win():
    # 两条约束必须同时成立：① 容器与子件都算会让每块料算两次（坑 F1，实测比值 2.0）；
    # ② 必须取**零件**而不是容器——容器的 Shape 是 Compound，没有矩阵惯性张量，
    # 取它会让惯量、主矩、逐零件归因在真实装配体上全部消失（四份公开装配体实测如此）。
    child_a = _FakeObj("PLATE-01", _FakeShape(1000.0))
    child_b = _FakeObj("PLATE-02", _FakeShape(500.0))
    # 容器体积故意写得与子件之和不相等：用来证明取的究竟是哪一个
    container = _FakeObj("ASSY-TOP", _FakeShape(9999.0), group=[child_a, child_b],
                         type_id="App::Part")
    snap = perf_snapshot.snapshot([container, child_a, child_b], "old")
    assert [o["label"] for o in snap["objects"]] == ["PLATE-01", "PLATE-02"]
    assert snap["n_objects"] == 2
    assert snap["total_volume_mm3"] == 1500.0
    assert snap["inertia_about_centroid_6"] is not None


def test_container_keeps_its_own_shape_when_no_child_can_produce_one():
    # 反例：容器自带 Shape，但子件里没有任何可产出的零件（基准/空容器）→ 必须用它自己，
    # 否则整份文件会变成"0 个对象"（静默漏掉真实几何）。
    datum = _FakeObj("X-axis", _FakeShape(0.0, n_solids=0), type_id="App::Line")
    container = _FakeObj("ASSY-TOP", _FakeShape(777.0), group=[datum], type_id="App::Part")
    snap = perf_snapshot.snapshot([container, datum], "old")
    assert snap["n_objects"] == 1
    assert snap["total_volume_mm3"] == 777.0


def test_container_without_a_shape_descends_into_its_children():
    child_a = _FakeObj("PLATE-01", _FakeShape(1000.0))
    child_b = _FakeObj("PLATE-02", _FakeShape(500.0))
    container = _FakeObj("ASSY-TOP", group=[child_a, child_b], type_id="App::Part")
    snap = perf_snapshot.snapshot([container, child_a, child_b], "old")
    assert snap["n_objects"] == 2
    assert snap["total_volume_mm3"] == 1500.0


def test_shells_and_degenerate_solids_are_counted_never_dropped_silently():
    shell = _FakeObj("LENS", _FakeShape(0.0, n_solids=0))
    degenerate = _FakeObj("DEGEN-09", _FakeShape(-3.0))
    good = _FakeObj("BRACKET-03", _FakeShape(100.0))
    snap = perf_snapshot.snapshot([shell, degenerate, good], "old")
    assert snap["n_zero_volume"] == 1
    assert snap["n_negative_volume"] == 1
    assert snap["n_objects"] == 1
    assert snap["total_volume_mm3"] == 100.0
    reasons = [item["reason"] for item in snap["unavailable"]]
    assert perf_snapshot.REASON_NON_POSITIVE_VOLUME in reasons


def test_object_type_outside_the_list_is_reported_never_dropped_silently():
    # 类型不在清单里但自带实体的对象（裸 Part::Compound 等）**不计数**（否则 PartDesign 的
    # Body 与其 tip 特征会各算一次），但必须留下痕迹：静默丢弃等于让「没提到」被读成
    # 「没有这个对象」（见 CONTRIBUTING.md 的 Code style）。
    compound = _FakeObj("BareCompound", _FakeShape(500.0), type_id="Part::Compound")
    snap = perf_snapshot.snapshot([compound], "old")
    assert snap["n_objects"] == 0
    assert [item["reason"] for item in snap["unavailable"]] == \
        [perf_snapshot.REASON_UNSUPPORTED_TYPE]


def test_zero_solid_datum_geometry_is_not_counted_as_a_shell():
    # 基准轴/面（App::Line / App::Plane）也是「有形状、0 实体」，但它们不是零件、
    # 也没有质量属性：计进「面/壳对象」只会给每份文件凭空加 6 条噪声。
    axis = _FakeObj("X-axis", _FakeShape(0.0, n_solids=0), type_id="App::Line")
    plane = _FakeObj("XY-plane", _FakeShape(0.0, n_solids=0), type_id="App::Plane")
    part = _FakeObj("PLATE-01", _FakeShape(100.0))
    snap = perf_snapshot.snapshot([axis, plane, part], "old")
    assert snap["n_objects"] == 1
    assert snap["n_zero_volume"] == 0
    assert snap["unavailable"] == []


def test_two_pass_parallel_axis_matches_the_analytic_value():
    # 两块不等重的"点质量"分列 ±x（V=2 @ +10、V=3 @ −10）：整体质心在 x=−2，
    # 于是绕 y/z 轴的惯量 = 2·12² + 3·8² = 480，而**绕原点**是 2·10² + 3·10² = 500。
    # 两个数字必须区分得开——这正是坑 F3 的判别式（单遍增量累加会给出 500）。
    # 同时它也钉住坑 F2：MatrixOfInertia 已是世界系，**不再乘 R·I·Rᵀ**。
    a = 10.0
    left = _FakeObj("MASS-01", _FakeShape(2.0, centroid=(a, 0.0, 0.0)))
    right = _FakeObj("MASS-02", _FakeShape(3.0, centroid=(-a, 0.0, 0.0)))
    snap = perf_snapshot.snapshot([left, right], "old")
    ixx, iyy, izz = snap["inertia_about_centroid_6"][:3]
    assert abs(snap["centroid_mm"][0] - (-2.0)) < 1e-9      # (2·10 + 3·(−10)) / 5 = −2
    assert abs(ixx) < 1e-9                                   # 质量都在 x 轴上 → 绕 x 轴为 0
    assert abs(iyy - 480.0) < 1e-9
    assert abs(izz - 480.0) < 1e-9


def test_bbox_is_the_union_of_the_parts():
    first = _FakeObj("A-01", _FakeShape(1.0, size=(10.0, 20.0, 30.0)))
    second = _FakeObj("B-02", _FakeShape(1.0, size=(40.0, 5.0, 30.0)))
    snap = perf_snapshot.snapshot([first, second], "old")
    assert snap["bbox_mm"] == [40.0, 20.0, 30.0]
    assert math.isclose(sum(snap["bbox_mm"]), 90.0)
