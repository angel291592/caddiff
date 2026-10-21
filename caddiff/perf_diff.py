r"""性能差异 —— 两份性能快照之间的纯逻辑比较（**不 import FreeCAD**）。

输入是 perf_snapshot.py 产出的两份快照，输出是 ``perf_diff.json``（字段契约见
docs/performance.md）。本模块只做算术：不取几何、不起子进程、不读文件。

【为什么差异不翻转退出码】退出码语义是「检出差异」，而性能数字是形状/BOM 变化的派生物：
体积/质心变了，本质上一定是几何层已经报了差异。让它参与判定，等于让「改了材质或参数」
这类改动把 CI 无缘无故变红（退出码契约的对外承诺就此破坏，见 README.md 的 "Exit codes"）。故本模块**永不**参与
``has_differences``。

【为什么质心位移要写成精确分摊，而不是 ``Σ m_i Δc_i``】后者会漏掉增件/删件与体积变化，
并制造「各项全为正、合计 100%」的假象。这里用的是恒等式
``Σ_i [(V1_i·c1_i − V0_i·c0_i − Cb·(V1_i − V0_i)) / Vb] == C1 − C0``
（``Cb``/``Vb`` 取两版中点），增删件天然被吸收，且每项可正可负、可以互相抵消。
"""
import math
import re

import perf_snapshot

# 相对容差默认值：只用来判「净位移是不是数值噪声」。实测同两个版本的重复导出，
# 质心差异在 1e-12 mm 量级，取 1e-6 的装配体尺度已足够宽松。
DEFAULT_REL_TOL = 1e-6

# 探针的时间预算默认值。单一真相源在 perf_snapshot（探针自己也要用它），这里只是给
# 「已经 import perf_diff 的调用方」一个别名，避免同一个数字在两处各写一遍。
DEFAULT_BUDGET_S = perf_snapshot.DEFAULT_BUDGET_S

# 探针的父进程死线余量：探针自己会在预算处停下，父进程只需再覆盖「两版 STP 的导入 + 进程
# 启动」这段不受预算约束的开销（实测导入 9 对象 1.0s / 193 对象 6.0s，大文件更久）。
PROBE_TIMEOUT_MARGIN_S = 300.0

# 主导轴的正负号用 locale-neutral 的 U+2212（同 i18n 铁律：方向 tag 不翻译，见 caddiff/i18n.py 顶部注释）。
MINUS = "\u2212"

# 分摊恒等式的自检容差与「把零贡献零件剔掉」的阈值（相对装配体尺度）。
_ALLOCATION_RTOL = 1e-9
_CONTRIBUTOR_RTOL = 1e-9

# 贡献排行只列前几名：读者要的是"谁干的"，不是一张全零件清单。
CONTRIBUTOR_LIMIT = 5

# ``unavailable[].reason`` 是机器读的枚举值，不翻译。
REASON_NO_INERTIA_TENSOR = "no_inertia_tensor"
REASON_NO_PRINCIPAL_MOMENTS = "no_principal_moments"
REASON_NO_VOLUME = "no_volume"
REASON_ALLOCATION_MISMATCH = "contributor_allocation_mismatch"

# 密度的单位换算（→ kg/mm³）。工程上常用 g/cm³，FreeCAD 内部单位是 kg/mm³，
# 两者差 1e6，写错就是一个离谱但"看起来像数字"的结果，所以必须显式认单位。
_DENSITY_UNITS = {
    "kg/mm3": 1.0, "kg/mm\u00b3": 1.0,
    "g/cm3": 1e-6, "g/cm\u00b3": 1e-6,
    "kg/m3": 1e-9, "kg/m\u00b3": 1e-9,
}
_DENSITY_RE = re.compile(r"^([0-9]*\.?[0-9]+(?:[eE][+-]?[0-9]+)?)(.*)$")

# 已知最致密的元素是锇（22.59 g/cm³ ≈ 2.26e-05 kg/mm³）。超过它的密度一定是单位写错了
# ——最常见的是把钢的 7.85 g/cm³ 写成 `--density 7.85`（不写单位按 kg/mm³ 读，差 1e6 倍）。
# 这是**提示**而不是拒绝：取值约定由调用方决定（见 docs/performance.md §1），但一个比锇还
# 致密 1e6 倍的密度不该悄悄算出一个 kg 数字。
MAX_PLAUSIBLE_DENSITY_KG_PER_MM3 = 2.3e-05


def parse_density(spec):
    """``--density`` 的取值 → kg/mm³。

    接受 ``7.85 g/cm3`` / ``7.85g/cm3`` / ``7.85e-06 kg/mm3`` / ``7850 kg/m3``；
    不写单位按 **kg/mm³**（FreeCAD 的内部单位）。认不出来一律抛 ValueError——CLI 侧
    argparse 会把它变成「参数错误 → 退出码 2」，绝不静默取一个默认密度。
    """
    text = str(spec).strip().lower().replace(" ", "")
    match = _DENSITY_RE.match(text)
    if not match:
        raise ValueError("density must look like '7.85 g/cm3' or '7.85e-06 kg/mm3'")
    value = float(match.group(1))
    unit = match.group(2) or "kg/mm3"
    if unit not in _DENSITY_UNITS:
        raise ValueError("unknown density unit %r (use one of: %s)"
                         % (unit, ", ".join(sorted(_DENSITY_UNITS))))
    kg_per_mm3 = value * _DENSITY_UNITS[unit]
    if not math.isfinite(kg_per_mm3) or kg_per_mm3 <= 0.0:
        raise ValueError("density must be a positive finite number")
    return kg_per_mm3


def _number(value, what):
    """取一个有限实数；不是数/不是有限值一律**拒绝**（NaN 静默传播会毁掉所有下游数字）。"""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError("%s: expected a number, got %r" % (what, value))
    result = float(value)
    if not math.isfinite(result):
        raise ValueError("%s: non-finite value %r" % (what, value))
    return result


def _vector3(value, what):
    if not isinstance(value, (list, tuple)) or len(value) != 3:
        raise ValueError("%s: expected 3 numbers, got %r" % (what, value))
    return [_number(v, what) for v in value]


def _norm(vec):
    return math.sqrt(sum(v * v for v in vec))


def _diag(bbox):
    """bbox 的对角线长度：当作「装配体尺度」用于把相对容差变成绝对容差。"""
    if not isinstance(bbox, (list, tuple)) or len(bbox) != 3:
        return 0.0
    try:
        return math.sqrt(sum(float(v) * float(v) for v in bbox))
    except (TypeError, ValueError):
        return 0.0


def dominant_axis(components):
    """位移向量的主导轴 → ``+X`` / ``−X`` / ``+Y`` / ``−Y`` / ``+Z`` / ``−Z``。

    why 不写「前/后/左/右」：上下前后是建模约定，本工具无从得知（与 docs/pipeline.md
    坑 F5 同源理由）；轴名与符号是 locale-neutral 的，任何语言下读法一致。
    """
    index = max(range(3), key=lambda i: abs(components[i]))
    sign = "+" if components[index] >= 0.0 else MINUS
    return sign + ("X", "Y", "Z")[index]


def _allocate(old, new, centroid_base, volume_base):
    """按 ``label`` 分摊质心位移；分母是两版平均体积（增删件按 0 计）。"""
    buckets = {}
    for side, snapshot in (("old", old), ("new", new)):
        for obj in snapshot.get("objects") or []:
            label = obj.get("label") or obj.get("base_name") or "?"
            volume = float(obj.get("volume_mm3") or 0.0)
            com = obj.get("centroid_mm") or [0.0, 0.0, 0.0]
            entry = buckets.get(label)
            if entry is None:
                entry = buckets[label] = {
                    "label": label,
                    "base_name": obj.get("base_name") or label,
                    "v_old": 0.0, "v_new": 0.0,
                    "m_old": [0.0, 0.0, 0.0], "m_new": [0.0, 0.0, 0.0],
                }
            key_v = "v_" + side
            key_m = "m_" + side
            entry[key_v] += volume
            for i in range(3):
                entry[key_m][i] += volume * float(com[i])
    allocation = []
    for entry in buckets.values():
        delta_v = entry["v_new"] - entry["v_old"]
        d_mm = [(entry["m_new"][i] - entry["m_old"][i] - centroid_base[i] * delta_v) / volume_base
                for i in range(3)]
        allocation.append({"label": entry["label"], "base_name": entry["base_name"],
                           "d_mm": d_mm})
    return allocation


def _contributors(allocation, scale):
    """按位移贡献的模取前 N 个；把数值噪声级的项剔掉。"""
    threshold = _CONTRIBUTOR_RTOL * max(scale, 1.0)
    items = [a for a in allocation if _norm(a["d_mm"]) > threshold]
    items.sort(key=lambda a: -_norm(a["d_mm"]))
    return items[:CONTRIBUTOR_LIMIT]


def _side_block(snapshot):
    """写进 perf_diff.json 的每版绝对值块。

    why 要有它：报告要显示「实体 CAD 体积 / 绕整体质心的体积惯量 / 主矩」这些**绝对值**
    （§2.3 的文案是这么写的），而报告的唯一输入是 manifest——绝对值必须随摘要一起带出来，
    不能让报告再去读另一份文件（否则就是两套口径；报告的唯一输入是 manifest，见 docs/pipeline.md 的 report.py 条目）。

    ``n_not_counted`` = 面/壳对象数 + ``unavailable`` 条目数（负体积等对象已在后者里，
    不重复计）。
    """
    return {
        "total_volume_mm3": snapshot.get("total_volume_mm3"),
        "total_area_mm2": snapshot.get("total_area_mm2"),
        "centroid_mm": snapshot.get("centroid_mm"),
        "inertia_about_centroid_6": snapshot.get("inertia_about_centroid_6"),
        "principal_moments": snapshot.get("principal_moments"),
        "bbox_mm": snapshot.get("bbox_mm"),
        "n_objects": int(snapshot.get("n_objects") or 0),
        "n_not_counted": int(snapshot.get("n_zero_volume") or 0)
                          + len(snapshot.get("unavailable") or []),
        "elapsed_s": snapshot.get("elapsed_s"),
    }


def _delta_vector(new_values, old_values, what, length, side_missing, unavailable):
    """逐分量求差；任一侧缺失/长度不对时返回 None 并**记下为什么**（不写 0 冒充）。"""
    if not (new_values and old_values and len(new_values) == length
            and len(old_values) == length):
        for side, values in (("old", old_values), ("new", new_values)):
            if not (values and len(values) == length):
                unavailable.append({"side": side, "label": None, "reason": side_missing})
        return None
    # 哪一侧含非有限值就记哪一侧：把「新版是 NaN」写成「旧版缺张量」等于给了错的排查方向。
    try:
        numbers_new = [_number(v, what) for v in new_values]
        numbers_old = [_number(v, what) for v in old_values]
    except ValueError:
        for side, values in (("old", old_values), ("new", new_values)):
            try:
                [_number(v, what) for v in values]
            except ValueError:
                unavailable.append({"side": side, "label": None, "reason": side_missing})
        return None
    return [n - o for n, o in zip(numbers_new, numbers_old)]


def diff(old, new, rel_tol=DEFAULT_REL_TOL, density_kg_per_mm3=None, budget=None):
    """两份快照 → 性能差异块。非法入参抛 ``ValueError``（由调用方转成退出码 2）。"""
    if rel_tol is None or rel_tol < 0.0 or not math.isfinite(float(rel_tol)):
        raise ValueError("rel_tol must be a finite non-negative number")

    volume_old = _number(old.get("total_volume_mm3"), "old total_volume_mm3")
    volume_new = _number(new.get("total_volume_mm3"), "new total_volume_mm3")
    centroid_old = _vector3(old.get("centroid_mm"), "old centroid_mm")
    centroid_new = _vector3(new.get("centroid_mm"), "new centroid_mm")
    if density_kg_per_mm3 is not None:
        density_kg_per_mm3 = _number(density_kg_per_mm3, "density")

    delta_volume = volume_new - volume_old
    # 近 0 基准算不出百分比：写 null，**不写 0 也不写 ∞**（两者都是错数字）。
    delta_volume_pct = (delta_volume / volume_old * 100.0) if volume_old > 0.0 else None

    shift_components = [centroid_new[i] - centroid_old[i] for i in range(3)]
    shift = _norm(shift_components)
    scale = max(_diag(old.get("bbox_mm")), _diag(new.get("bbox_mm")))
    if scale > 0.0:
        unchanged = shift <= rel_tol * scale
    else:
        unchanged = shift == 0.0

    degraded_reason = None
    contributors = []
    if not unchanged:
        volume_base = (volume_old + volume_new) / 2.0
        centroid_base = [(centroid_old[i] + centroid_new[i]) / 2.0 for i in range(3)]
        if volume_base <= 0.0:
            degraded_reason = REASON_NO_VOLUME
        else:
            allocation = _allocate(old, new, centroid_base, volume_base)
            contributors = _contributors(allocation, scale)
            gap = max(abs(sum(a["d_mm"][i] for a in allocation) - shift_components[i])
                      for i in range(3))
            if gap > _ALLOCATION_RTOL * max(scale, 1.0):
                # 恒等式不成立说明分摊算错了（不是"精度不够"）：照实报降级，不静默给一半答案。
                degraded_reason = REASON_ALLOCATION_MISMATCH

    unavailable = []
    for side, snapshot in (("old", old), ("new", new)):
        for item in snapshot.get("unavailable") or []:
            unavailable.append({"side": side,
                                "label": item.get("label"),
                                "reason": item.get("reason")})
    delta_inertia = _delta_vector(new.get("inertia_about_centroid_6"),
                                  old.get("inertia_about_centroid_6"),
                                  "inertia_about_centroid_6", 6,
                                  REASON_NO_INERTIA_TENSOR, unavailable)
    delta_principal = _delta_vector(new.get("principal_moments"), old.get("principal_moments"),
                                    "principal_moments", 3,
                                    REASON_NO_PRINCIPAL_MOMENTS, unavailable)
    for side, snapshot in (("old", old), ("new", new)):
        if snapshot.get("inertia_about_centroid_6") and not snapshot.get("principal_moments"):
            unavailable.append({"side": side, "label": None,
                                "reason": REASON_NO_PRINCIPAL_MOMENTS})

    if density_kg_per_mm3 is None:
        # 【不猜密度】没有显式确认的密度就一个 kg 都不出现（ShapeMaterial.Density 在真实
        # STEP 里恒为 FreeCAD Default，照抄就是"钢件按水算"的静默错数字，坑 F8）。
        mass_old = mass_new = delta_mass = None
    else:
        mass_old = volume_old * density_kg_per_mm3
        mass_new = volume_new * density_kg_per_mm3
        delta_mass = mass_new - mass_old

    effective_budget = budget if budget is not None else perf_snapshot.DEFAULT_BUDGET_S
    return {
        "delta_volume_mm3": delta_volume,
        "delta_volume_pct": delta_volume_pct,
        "mass_kg_old": mass_old,
        "mass_kg_new": mass_new,
        "delta_mass_kg": delta_mass,
        "centroid_shift_mm": shift,
        "centroid_shift_components": shift_components,
        "dominant_axis": None if unchanged else dominant_axis(shift_components),
        "delta_inertia_6": delta_inertia,
        "delta_principal_moments": delta_principal,
        "contributors": contributors,
        "old": _side_block(old),
        "new": _side_block(new),
        "settings": {
            "rel_tol": float(rel_tol),
            "budget": float(effective_budget),
            "density_source": "cli" if density_kg_per_mm3 is not None else "none",
            "density_kg_per_mm3": density_kg_per_mm3,
        },
        "degraded_reason": degraded_reason,
        "unavailable": unavailable,
    }
