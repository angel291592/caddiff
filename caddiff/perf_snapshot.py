r"""性能快照 —— 从一个 FreeCAD 文档的对象树里取出「体积类」质量属性。

【为什么这里不 import FreeCAD】本模块必须能被**系统 Python** 直接 import 做纯逻辑测试
（tests/unit/test_perf_diff.py）；FreeCAD 只在 perf_probe.py 里 import。因此本模块对传进来
的对象只做鸭子类型访问（``obj.Shape`` / ``shp.Volume`` …），自己不碰任何 FreeCAD 模块。

【为什么全部用体积、不用质量】真实公开 STEP 里所有对象的 ``ShapeMaterial.Density`` 都是
FreeCAD Default 值（坑 F8 实测），照抄会给出「钢件按水算」的静默错数字。故这里只算
**单位密度**量：体积、体积加权中心、绕整体质心的体积惯量（mm⁵）。质量只在调用方显式给出
密度时才算，见 perf_diff.py。

【单位与坐标基】长度 mm、面积 mm²、体积 mm³、体积惯量 mm⁵（= mm³·mm²）。
所有量都在**世界系**：``MatrixOfInertia`` 已经是世界系且嵌套 Placement 已合成（坑 F2），
**不要再乘 R·I·Rᵀ**（那会把旋转做两遍）。

【数值口径】非有限值、负体积、取不到质心的对象一律不计入并且**不取绝对值掩盖**，
记入 ``unavailable``；面/壳对象（无实体）单独计数。逐条理由见下面的 reason 枚举。
"""
import math
import time

# 探针的时间预算（每个版本各自一份）。取值依据：逐对象 Volume+质心实测 19–45 ms（坑 F9），
# 300s 足以覆盖上万对象；预算用完的对象记 budget_exceeded，绝不静默丢弃。
DEFAULT_BUDGET_S = 300.0

# 只接受这两类对象：STEP 导入产出 Part::Feature；App::Part 是装配容器，它的 Shape
# 已经是全部子件的并集（坑 F1 —— 容器与子件都算会把每块料算两次，实测比值 2.0/2.0/4.0）。
GEOMETRY_TYPES = ("Part::Feature", "App::Part")

# ``unavailable[].reason`` 是机器读的枚举值，**不翻译**（AGENTS.md §4）。
REASON_NO_SHAPE = "no_shape"
REASON_NO_CENTROID = "no_centroid"
REASON_NON_FINITE = "non_finite"
REASON_BUDGET_EXCEEDED = "budget_exceeded"
REASON_NEGATIVE_VOLUME = "negative_volume"

# 体积守恒自检的相对容差：仅用于发现「累加路径被改坏」，正常浮点累加远小于它。
_CONSERVATION_RTOL = 1e-9


def label_of(obj):
    """对象标签（报告与 JSON 里给用户看的名字）。取不到就退到 Name，再取不到给 "?"。"""
    for attr in ("Label", "Name"):
        value = getattr(obj, attr, None)
        if value:
            return str(value)
    return "?"


def base_name(name):
    """族名：截掉末尾从最后一个字母字符往后的内容。

    与 bom_diff.base_name / geom_diff.base_name 同一条规则（三处必须一致，否则报告里的
    零件名对不上）；这里不能 import bom_diff —— 本模块要能在 FreeCAD 解释器里跑，而
    bom_diff 只在系统 Python 下被导入过，跨解释器复用同一个模块并不成立，故就地重写这一行。

    ⚠️ 它只给**族名**，不单独决定是否折叠（那是 BOM 层 align_keys 的职责，见决策 D-019）。
    本模块只把它当展示用的字段，**不用它做聚合 key** —— 折叠错了两版零件会被并成一个。
    """
    last_alpha = max((i for i, c in enumerate(name) if c.isalpha()), default=len(name) - 1)
    return name[: last_alpha + 1]


def children_of(obj):
    """容器子对象。App::Part 走 Group；Part::Feature 没有 Group（返回空表）。"""
    kids = getattr(obj, "Group", None)
    return list(kids) if kids else []


def shape_of(obj):
    """取 Shape；取不到或空形状返回 None（判据与 geom_diff.get_shape 一致）。"""
    try:
        shp = obj.Shape
    except Exception:                                     # noqa: BLE001
        return None
    if shp is None:
        return None
    try:
        if shp.isNull():
            return None
    except Exception:                                     # noqa: BLE001
        pass
    return shp


def walk_objects(objects):
    """深度优先遍历对象树，逐个产出 ``(obj, shape)``；**只取叶子**。

    【遍历规则（坑 F1 + 实测）】两份清单都要满足：
    ① **不能重复计数**：顶层容器的 Shape 是全部子件的并集，容器与子件都算会让每块料算两次
       （实测比值 2.0/2.0/4.0）。
    ② **必须逐零件**：容器（``App::Part``）的 Shape 是个 Compound，**它没有 MatrixOfInertia**。
       若「自带 Shape 就不再下钻」，真实装配体上会只剩 1 个对象——实测四份公开装配体
       （Go Kart / XYZ / ekvc / Voron-0 总成）全都如此：逐零件归因与惯性张量全部消失。
    故规则是「有 Shape 且子件里没有可产出的形状」才取自己；子件里有零件时一律下钻。
    实测两种清单的总量一致到 1e-3 以内（plan 的 F1 记录比值 1.0000），下钻那份是"各零件
    体积之和"，也正是既有决策 D-015 的口径（容器被视为子件并集的重复，不参与比对）。

    只从**根对象**开始走（没有父对象的那些），否则容器与其子件会被各算一次。
    ``shape`` 为 None 表示「该对象是几何类型但既没有可用 Shape，子件里也没有任何产出」，
    由调用方记 ``no_shape``。
    """
    by_id = {id(o): o for o in objects}
    kids = {}
    for obj in objects:
        kids[id(obj)] = [c for c in children_of(obj) if id(c) in by_id]
    has_parent = set()
    for _id, children in kids.items():
        for child in children:
            has_parent.add(id(child))

    yields_cache = {}

    def yields(obj):
        """该对象或其子树里是否存在「可产出」的对象（几何类型且自带 Shape）。"""
        oid = id(obj)
        if oid in yields_cache:
            return yields_cache[oid]
        yields_cache[oid] = False          # 先占位：环状 Group 不该让这里无限递归
        if getattr(obj, "TypeId", "") in GEOMETRY_TYPES and shape_of(obj) is not None:
            yields_cache[oid] = True
            return True
        result = any(yields(child) for child in kids.get(oid, ()))
        yields_cache[oid] = result
        return result

    seen = set()

    def visit(obj):
        oid = id(obj)
        if oid in seen:            # 防御：Group 理论上不成环，但坏文档不该让探针炸掉
            return
        seen.add(oid)
        children = kids.get(oid, ())
        if getattr(obj, "TypeId", "") not in GEOMETRY_TYPES:
            # 非几何对象（草图 / 基准 / 未知容器）自身没有质量属性；有 Group 就继续下钻。
            for child in children:
                yield from visit(child)
            return
        shape = shape_of(obj)
        if shape is not None and not any(yields(child) for child in children):
            yield obj, shape
            return
        produced = False
        for child in children:
            for item in visit(child):
                produced = True
                yield item
        if not produced:
            yield obj, None

    for obj in objects:
        if id(obj) in has_parent:
            continue
        yield from visit(obj)


def centroid_of(shp):
    """体积中心 + 用了哪一级回退。返回 ``([x, y, z], source)``。

    三级回退（坑 F6）：``Part::Compound`` 上 ``CenterOfMass`` / ``StaticMoments`` 会抛
    ``AttributeError``，但 ``CenterOfGravity`` 与 ``Volume`` 可用；三级全挂的对象由调用方
    记 ``no_centroid``（真实语料里确实存在取不到质心的对象）。
    均匀密度下 ``CenterOfGravity`` 就是体积中心，与 ``CenterOfMass`` 同义。
    """
    try:
        com = shp.CenterOfMass
        return [float(com.x), float(com.y), float(com.z)], "CenterOfMass"
    except AttributeError:
        pass
    try:
        com = shp.CenterOfGravity
        return [float(com.x), float(com.y), float(com.z)], "CenterOfGravity"
    except AttributeError:
        pass
    moments = shp.StaticMoments
    volume = float(shp.Volume)
    return [float(moments[0]) / volume, float(moments[1]) / volume,
            float(moments[2]) / volume], "StaticMoments/Volume"


def tensor6_of(shp):
    """世界系、绕**自身质心**的惯性张量，单位密度 → ``[ixx,iyy,izz,ixy,ixz,iyz]``。

    取不到返回 None（``Part::Compound`` 会抛 AttributeError，坑 F6）；非有限值也返回 None。
    """
    try:
        matrix = shp.MatrixOfInertia
    except Exception:                                     # noqa: BLE001
        return None
    try:
        values = [float(matrix.A11), float(matrix.A22), float(matrix.A33),
                  float(matrix.A12), float(matrix.A13), float(matrix.A23)]
    except (AttributeError, TypeError, ValueError):
        return None
    if not all(math.isfinite(v) for v in values):
        return None
    return values


def _mat3_from_tensor6(t6):
    ixx, iyy, izz, ixy, ixz, iyz = t6
    return [[ixx, ixy, ixz], [ixy, iyy, iyz], [ixz, iyz, izz]]


def _jacobi_eigen(matrix, max_sweeps=64):
    """对称 3×3 的 Jacobi 特征分解。返回 ``(特征值, 特征向量矩阵)`` 或 None。

    为什么自己写：本包核心零第三方依赖（AGENTS.md §3 边界），numpy 不能引；3×3 对称矩阵
    的 Jacobi 旋转足够精确且确定性，且**不做任何排序**——排序在主矩里另有含义（降序），
    把两件事混在一个函数里最容易出错。
    """
    a = [list(row) for row in matrix]
    v = [[1.0, 0.0, 0.0], [0.0, 1.0, 0.0], [0.0, 0.0, 1.0]]
    for _ in range(max_sweeps):
        off = abs(a[0][1]) + abs(a[0][2]) + abs(a[1][2])
        diag = abs(a[0][0]) + abs(a[1][1]) + abs(a[2][2])
        if off <= 1e-15 * max(diag, 1e-300):
            break
        for p, q in ((0, 1), (0, 2), (1, 2)):
            apq = a[p][q]
            if apq == 0.0:
                continue
            theta = (a[q][q] - a[p][p]) / (2.0 * apq)
            # t = sgn(θ)/(|θ|+√(θ²+1))；θ=0 时取 t=1（此时 2θ 的符号无意义，任取一支）
            if theta == 0.0:
                t = 1.0
            else:
                t = math.copysign(1.0, theta) / (abs(theta) + math.sqrt(theta * theta + 1.0))
            c = 1.0 / math.sqrt(t * t + 1.0)
            s = t * c
            for k in range(3):
                if k == p or k == q:
                    continue
                akp, akq = a[k][p], a[k][q]
                a[k][p] = a[p][k] = c * akp - s * akq
                a[k][q] = a[q][k] = s * akp + c * akq
            app, aqq = a[p][p], a[q][q]
            a[p][p] = app - t * apq
            a[q][q] = aqq + t * apq
            a[p][q] = a[q][p] = 0.0
            for k in range(3):
                vkp, vkq = v[k][p], v[k][q]
                v[k][p] = c * vkp - s * vkq
                v[k][q] = s * vkp + c * vkq
    values = [a[0][0], a[1][1], a[2][2]]
    if not all(math.isfinite(x) for x in values):
        return None
    return values, v


def principal_valid(lambs, rel_tol=1e-9):
    """惯性张量的物理有效性：三主矩非负，且满足三角不等式 ``λ_i ≤ λ_j + λ_k``。

    why 必须带相对容差：细长杆的真实主矩就是 (大, 小, 小) 且 ``λ_max ≈ λ_mid + λ_min``
    （极限情况取等号），用严格小于会把合法零件判成坏张量。不满足时**不取绝对值掩盖**
    （取绝对值会把一个坏张量伪装成合法结果），由调用方记为不可用。
    """
    if not lambs or len(lambs) != 3:
        return False
    try:
        values = [float(x) for x in lambs]
    except (TypeError, ValueError):
        return False
    if not all(math.isfinite(x) for x in values):
        return False
    l1, l2, l3 = values                      # 调用方保证降序
    slack = rel_tol * max(abs(l1), abs(l2), abs(l3), 1e-300)
    if l3 < -slack:
        return False
    if l1 > l2 + l3 + slack:
        return False
    return True


def principal_from_tensor(t6, rel_tol=1e-9):
    """张量 → ``(主矩降序, 主轴)``；张量不可用/不合法时返回 ``(None, None)``。

    ⚠️ 主矩必须由**聚合后**的张量求特征值，**不能把各件的主矩逐项相加**，也不能用
    ``PrincipalProperties.Moments`` 的轴序去对应 A11/A22/A33（坑 F5：两套顺序不同，
    实测误用差 50 倍）。
    """
    if not t6 or len(t6) != 6:
        return None, None
    try:
        matrix = _mat3_from_tensor6([float(x) for x in t6])
    except (TypeError, ValueError):
        return None, None
    if not all(math.isfinite(x) for row in matrix for x in row):
        return None, None
    eig = _jacobi_eigen(matrix)
    if eig is None:
        return None, None
    values, vectors = eig
    order = sorted(range(3), key=lambda i: -values[i])
    lambs = [values[i] for i in order]
    if not principal_valid(lambs, rel_tol=rel_tol):
        return None, None
    axes = [[vectors[row][i] for row in range(3)] for i in order]
    return lambs, axes


def _empty_bbox():
    return [None, None, None, None, None, None]


def _bbox_extend(box, bbox):
    for i in range(3):
        low = getattr(bbox, ("XMin", "YMin", "ZMin")[i])
        high = getattr(bbox, ("XMax", "YMax", "ZMax")[i])
        if box[i] is None or low < box[i]:
            box[i] = low
        if box[i + 3] is None or high > box[i + 3]:
            box[i + 3] = high


def snapshot(objects, side, budget_s=DEFAULT_BUDGET_S):
    """把一版文档的对象树变成一份性能快照（字段契约见 docs/performance.md）。

    ``side`` 是 "old" / "new"（写进 JSON，供下游区分两版）。
    逐对象失败一律降级为 ``unavailable`` 条目，**不让一个坏零件毁掉整次查询**。
    """
    started = time.time()
    parts = []
    unavailable = []
    n_zero_volume = 0
    n_negative_volume = 0
    box = _empty_bbox()

    for obj, shp in walk_objects(objects):
        name = label_of(obj)
        if shp is None:
            unavailable.append({"label": name, "reason": REASON_NO_SHAPE})
            continue
        # 预算检查放在任何真正的几何查询之前——超预算后再查 Volume 就等于预算没生效。
        if time.time() - started > budget_s:
            unavailable.append({"label": name, "reason": REASON_BUDGET_EXCEEDED})
            continue
        try:
            n_solids = len(shp.Solids)
        except Exception:                                 # noqa: BLE001
            unavailable.append({"label": name, "reason": REASON_NO_SHAPE})
            continue
        if n_solids == 0:
            # 面/壳对象：没有可比的质量属性，单独计数（真实语料里 193 个对象中 189 个是它，坑 F7）
            n_zero_volume += 1
            continue
        try:
            volume = float(shp.Volume)
        except Exception:                                 # noqa: BLE001
            unavailable.append({"label": name, "reason": REASON_NO_CENTROID})
            continue
        if not math.isfinite(volume):
            unavailable.append({"label": name, "reason": REASON_NON_FINITE})
            continue
        if volume <= 0.0:
            # 负体积 / 零体积是退化几何：**不取 abs 掩盖**（取了就会把它当正常实体累加）
            n_negative_volume += 1
            unavailable.append({"label": name, "reason": REASON_NEGATIVE_VOLUME})
            continue
        try:
            centroid, centroid_source = centroid_of(shp)
        except Exception:                                 # noqa: BLE001
            unavailable.append({"label": name, "reason": REASON_NO_CENTROID})
            continue
        try:
            area = float(shp.Area)
            bbox = shp.BoundBox
            bbox_mm = [float(bbox.XLength), float(bbox.YLength), float(bbox.ZLength)]
        except Exception:                                 # noqa: BLE001
            unavailable.append({"label": name, "reason": REASON_NO_CENTROID})
            continue
        if not (all(math.isfinite(v) for v in centroid)
                and math.isfinite(area) and all(math.isfinite(v) for v in bbox_mm)):
            unavailable.append({"label": name, "reason": REASON_NON_FINITE})
            continue
        _bbox_extend(box, bbox)
        parts.append({
            "label": name,
            "base_name": base_name(name),
            "volume_mm3": volume,
            "area_mm2": area,
            "bbox_mm": bbox_mm,
            "centroid_mm": centroid,
            "centroid_source": centroid_source,
            "tensor_6": tensor6_of(shp),
        })

    total_volume = sum(p["volume_mm3"] for p in parts)
    total_area = sum(p["area_mm2"] for p in parts)
    if total_volume > 0.0:
        centroid = [sum(p["volume_mm3"] * p["centroid_mm"][i] for p in parts) / total_volume
                    for i in range(3)]
    else:
        centroid = [0.0, 0.0, 0.0]

    # 【两遍累积（坑 F3）】必须先求出整体质心 C，再按 (c_i − C) 做平行轴：
    #   I_C = Σ [ I_cm_i + V_i (|c_i−C|² E − (c_i−C)(c_i−C)ᵀ) ]
    # 增量式（每轮换参考点）实测让 Jyy 偏 M·50²。张量本身已是世界系，不再做旋转（坑 F2）。
    inertia = None
    if parts and all(p["tensor_6"] is not None for p in parts):
        inertia = [0.0] * 6
        for p in parts:
            dx = p["centroid_mm"][0] - centroid[0]
            dy = p["centroid_mm"][1] - centroid[1]
            dz = p["centroid_mm"][2] - centroid[2]
            volume = p["volume_mm3"]
            d2 = dx * dx + dy * dy + dz * dz
            t6 = p["tensor_6"]
            inertia[0] += t6[0] + volume * (d2 - dx * dx)
            inertia[1] += t6[1] + volume * (d2 - dy * dy)
            inertia[2] += t6[2] + volume * (d2 - dz * dz)
            inertia[3] += t6[3] - volume * dx * dy
            inertia[4] += t6[4] - volume * dx * dz
            inertia[5] += t6[5] - volume * dy * dz
        if not all(math.isfinite(v) for v in inertia):
            inertia = None

    if inertia is None:
        principal_moments, principal_axes = None, None
    else:
        principal_moments, principal_axes = principal_from_tensor(inertia)

    _check_conservation(parts, total_volume, centroid)

    if box[0] is None:
        bbox_mm = [0.0, 0.0, 0.0]
    else:
        bbox_mm = [float(box[i + 3] - box[i]) for i in range(3)]

    return {
        "side": side,
        "n_objects": len(parts),
        "n_zero_volume": n_zero_volume,
        "n_negative_volume": n_negative_volume,
        "total_volume_mm3": total_volume,
        "total_area_mm2": total_area,
        "centroid_mm": centroid,
        "inertia_about_centroid_6": inertia,
        "principal_moments": principal_moments,
        "principal_axes": principal_axes,
        "bbox_mm": bbox_mm,
        "objects": parts,
        "unavailable": unavailable,
        "elapsed_s": round(time.time() - started, 3),
    }


def _check_conservation(parts, total_volume, centroid):
    """自检：逐件体积之和与总量、以及一阶矩与 ``总量×质心`` 必须一致。

    这条恒等式在数学上是恒真的，所以它抓的不是"逻辑错"，而是**将来的重构把累加路径改坏**
    （例如有人改成用容器 Shape 的体积当总量、或先算质心再累加体积）。不满足就打到控制台
    （英文，控制台输出不进 i18n 表）；数值本身不写进产物，避免下游把自检值当结论。
    """
    if not parts or total_volume <= 0.0:
        return
    summed = sum(p["volume_mm3"] for p in parts)
    gap = abs(summed - total_volume)
    for i in range(3):
        moment = sum(p["volume_mm3"] * p["centroid_mm"][i] for p in parts)
        gap = max(gap, abs(moment - total_volume * centroid[i]))
    if gap > _CONSERVATION_RTOL * total_volume:
        print(f"  WARN: volume/centroid self-check gap {gap:.3e} "
              f"(total {total_volume:.3f} mm3) — the accumulation may be broken")
