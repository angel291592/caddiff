r"""
几何层差异对比：对BOM diff的CANDIDATE逐一做几何对比。
策略：先按体积/bbox/位姿三态分类，identical则跳过布尔运算；moved直接记为位移类差异
（不做布尔）；其余走布尔对称差。
必须用FreeCAD自带Python解释器运行：
    <FreeCAD>\bin\python.exe geom_diff.py <bom_diff.json> <stp_old> <stp_new> <out_json>
        [--max-faces N] [--boolean-timeout S] [--skip-parts A,B] [--min-diff-pct P]
"""
import argparse
import json
import math
import shutil
import subprocess
import sys
import os
import tempfile
import time as _time
from collections import defaultdict

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
if SCRIPT_DIR not in sys.path:
    sys.path.insert(0, SCRIPT_DIR)

import console  # noqa: E402
import fcenv  # noqa: E402  （路径解析的唯一真相源，必须先于 import FreeCAD）
import i18n  # noqa: E402

console.enable_utf8_output()

# FreeCAD 模块的 bin/lib 由解释器自身位置推导；找不到就不注入（Linux 在系统 site-packages 里）。
FC_BIN, FC_LIB = fcenv.inject_freecad_paths()

import FreeCAD  # noqa: E402
import Import  # noqa: E402

VOLUME_THRESHOLD = 0.001
VOLUME_TOLERANCE = 0.01
BBOX_TOLERANCE = 0.01

# 位姿容差：小于此值视为同位置/同姿态（STEP 导出的浮点舍入噪声量级）。
# 实测本装配体 62 个已配对实例，中心位移全部 ≤0.2017mm 且其中 58 个 <0.0002mm，
# 而真实的形状改动体积差在 1.67~8.34mm³ 量级——两类信号量级差得很开。
MOVE_TOLERANCE_MM = 0.01
ROT_TOLERANCE_DEG = 0.05

# 布尔运算闸门。两道都需要，缺一不可：
# - MAX_FACES_FOR_BOOLEAN 是事前的廉价拦截，主要挡住"顶层装配体被当成零件候选"这类
#   巨型 Shape（实测 ASSY-TOP… 有 8528 面）。
# - BOOLEAN_TIMEOUT_S 才是真正的保护：面数与布尔耗时【不成正比】，实测
#   PART-B 仅 912 面却单次 cut 跑过 840s 未返回，任何面数闸门都拦不住它。
#   故布尔一律在子进程里跑（见 boolean_worker.py），超时即 kill。
MAX_FACES_FOR_BOOLEAN = 5000
BOOLEAN_TIMEOUT_S = 60

# 相对阈值：差异体积须大于零件体积的该百分比才算差异。
# 默认 0（不启用）——本案例最小真差异占比 0.35%，设任何非零默认值都可能筛掉真差异。
# 这是给大装配体的可选工具（CLI --min-diff-pct），不是默认行为。
VOLUME_THRESHOLD_PCT = 0.0

# 整体配准检查阈值。新版 STP 若整体坐标系被平移/旋转，会让每个零件都算出巨大伪差异。
# 判据是"已配对零件的位移是否既够大又方向一致"，见 check_global_alignment
# （用整体 bbox 判会把"增删零件"误判成"整体平移"，已实测——那是被推翻的第一版实现）。
GLOBAL_ALIGN_TOLERANCE_MM = 1.0
GLOBAL_ALIGN_CONSISTENCY = 0.8    # 位移方向一致性下限：整体平移≈1，零散移动≈0

BOOLEAN_WORKER = os.path.join(SCRIPT_DIR, "boolean_worker.py")
# 本脚本自己就跑在 FreeCAD 解释器里，故子进程默认沿用同一个解释器；
# 显式设了 FREECAD_PYTHON 时以它为准（便于用另一份 FreeCAD 跑布尔）。
FC_PYTHON = (os.environ.get("FREECAD_PYTHON") or "").strip() or sys.executable
FC_BIN, FC_LIB = fcenv.derive_bin_lib(FC_PYTHON)


def base_name(name):
    last_alpha = max((i for i, c in enumerate(name) if c.isalpha()), default=len(name) - 1)
    return name[: last_alpha + 1]



def get_parent_chain(obj):
    chain = []
    visited = set()
    current = obj
    while current:
        cid = id(current)
        if cid in visited:
            break
        visited.add(cid)
        chain.append(current.Label)
        if hasattr(current, 'InList') and current.InList:
            current = current.InList[0]
        else:
            break
    return list(reversed(chain))


def get_bbox_dict(bbox):
    return {
        "x_min": bbox.XMin, "y_min": bbox.YMin, "z_min": bbox.ZMin,
        "x_max": bbox.XMax, "y_max": bbox.YMax, "z_max": bbox.ZMax,
        "center": [bbox.Center.x, bbox.Center.y, bbox.Center.z],
    }


def build_label_map(doc):
    label_map = defaultdict(list)
    for obj in doc.Objects:
        if obj.TypeId in ("Part::Feature", "App::Part"):
            bn = base_name(obj.Label)
            label_map[bn].append(obj)
    return label_map


def get_shape(obj):
    """Part::Feature和App::Part都能直接访问.Shape（App::Part返回其Group的聚合Shape）。
    某个同名部件在新旧版本间被Creo重构为子装配容器时（如PART-A-703 Part::Feature -> App::Part），
    仍需保留它参与配对，否则该部件的真实几何差异会被静默漏检。"""
    try:
        shp = obj.Shape
        if shp is None or shp.isNull():
            return None
        return shp
    except Exception:
        return None


def pair_by_proximity(old_objs, new_objs):
    """按bbox中心距离+体积相似度联合评分做最近邻配对（贪心）。
    体积差异过大的候选对会被过滤掉，避免"中心距离最近但根本不是同一物理部件"的误配对
    （已实测：仅用距离配对时，一个体积341mm³的壳体会被错配到一个体积75mm³的薄片上）。
    """
    old_features = [(o, get_shape(o)) for o in old_objs]
    old_features = [(o, s) for o, s in old_features if s is not None]
    new_features = [(o, get_shape(o)) for o in new_objs]
    new_features = [(o, s) for o, s in new_features if s is not None]

    paired = []
    unpaired_old = []
    used_new = set()
    for old_obj, old_shape in old_features:
        old_center = old_shape.BoundBox.Center
        old_vol = old_shape.Volume
        best = None
        best_dist = float('inf')
        for i, (new_obj, new_shape) in enumerate(new_features):
            if i in used_new:
                continue
            new_vol = new_shape.Volume
            # 体积相差超过50%，大概率不是同一部件，跳过（避免壳体误配到薄片这类错配）
            if old_vol > 0 and abs(old_vol - new_vol) / old_vol > 0.5:
                continue
            dist = old_center.distanceToPoint(new_shape.BoundBox.Center)
            if dist < best_dist:
                best_dist = dist
                best = (i, new_obj)
        if best is not None:
            used_new.add(best[0])
            paired.append((old_obj, best[1]))
        else:
            unpaired_old.append(old_obj)
    return paired, unpaired_old


def placement_delta(old_shape, new_shape):
    """返回 {"translation_mm", "rotation_deg", "rotation_detected_by"}。

    平移量用两 Shape 的 BoundBox.Center 距离，【不用 obj.Placement】：App::Part 的聚合
    Shape 已含容器变换，而 Placement 只是局部量，两者不可混用；BoundBox 中心是唯一对
    Part::Feature 与 App::Part 两种 TypeId 都成立的量（坑 D1 的同源问题）。

    旋转量优先用 Shape.Placement.Rotation 求相对旋转角；取不到时退化为
    "bbox 三边长多重集相同但排列不同"作为【充分不必要】判据，并标 bbox_permutation
    说明置信度较低（该判据对绕轴 90° 的整数倍之外的旋转不敏感）。
    """
    out = {"translation_mm": 0.0, "rotation_deg": 0.0, "rotation_detected_by": "placement"}
    try:
        out["translation_mm"] = float(
            old_shape.BoundBox.Center.distanceToPoint(new_shape.BoundBox.Center))
    except Exception:
        out["translation_mm"] = 0.0

    try:
        rel = old_shape.Placement.Rotation.inverted().multiply(new_shape.Placement.Rotation)
        out["rotation_deg"] = abs(math.degrees(rel.Angle))
        return out
    except Exception:
        pass

    # 退化判据：三边长排序后一致但原始排列不同 → 大概率被绕轴旋转过
    try:
        bba, bbb = old_shape.BoundBox, new_shape.BoundBox
        a = [bba.XLength, bba.YLength, bba.ZLength]
        b = [bbb.XLength, bbb.YLength, bbb.ZLength]
        same_sorted = all(abs(x - y) <= BBOX_TOLERANCE
                          for x, y in zip(sorted(a), sorted(b)))
        same_order = all(abs(x - y) <= BBOX_TOLERANCE for x, y in zip(a, b))
        out["rotation_detected_by"] = "bbox_permutation"
        out["rotation_deg"] = 90.0 if (same_sorted and not same_order) else 0.0
    except Exception:
        out["rotation_detected_by"] = "unavailable"
    return out


def classify_change(old_shape, new_shape):
    """三态分类，返回 (kind, delta)：

      "identical"     体积/bbox/位置/姿态均在容差内 → 唯一可静默跳过的档
      "moved"         体积与 bbox 三边长（排序后）一致，但平移或旋转超容差
                      → 不做布尔运算，直接记为位移类差异
      "shape_changed" 其余 → 走布尔对称差

    why 必须有 moved 这一档：原 shapes_similar() 只比 Volume 与 BoundBox 三边长，
    这两个量对平移【完全不变】、对 90°/180° 旋转部分不变。实测
    shapes_similar(box, box平移50mm) == True 会被判"无差异"跳过，而其真实对称差
    = 200mm³ = 零件体积的 2 倍（两形状零重叠）。那是静默漏检，比报错危险。

    why moved 的条件要严：判据要求体积【与】bbox 三边长排序后【均】一致，
    条件不满足就落到 shape_changed（安全侧——宁可多做一次布尔，不可把形状改动
    误报成"只是挪了个位置"）。
    """
    delta = {"translation_mm": 0.0, "rotation_deg": 0.0,
             "rotation_detected_by": "unavailable"}
    try:
        va, vb = old_shape.Volume, new_shape.Volume
        bba, bbb = old_shape.BoundBox, new_shape.BoundBox
    except Exception:
        return "shape_changed", delta

    delta = placement_delta(old_shape, new_shape)
    vol_same = abs(va - vb) <= VOLUME_TOLERANCE
    dims_old = sorted([bba.XLength, bba.YLength, bba.ZLength])
    dims_new = sorted([bbb.XLength, bbb.YLength, bbb.ZLength])
    dims_same = all(abs(x - y) <= BBOX_TOLERANCE for x, y in zip(dims_old, dims_new))

    if not (vol_same and dims_same):
        return "shape_changed", delta
    if (delta["translation_mm"] > MOVE_TOLERANCE_MM
            or delta["rotation_deg"] > ROT_TOLERANCE_DEG):
        return "moved", delta
    return "identical", delta


def shape_too_complex(shape, max_faces=None):
    """事前的廉价闸门。注意它【拦不住所有挂死】——面数与布尔耗时不成正比
    （实测 912 面的零件单次 cut 超过 840s）。真正的保护是 symmetric_diff_isolated
    的子进程硬超时，这里只用于挡住巨型 Shape 以省掉一次昂贵的 BREP 落盘。"""
    limit = MAX_FACES_FOR_BOOLEAN if max_faces is None else max_faces
    try:
        return len(shape.Faces) > limit
    except Exception:
        return True


def face_count(shape):
    try:
        return len(shape.Faces)
    except Exception:
        return -1


def symmetric_diff_isolated(old_shape, new_shape, timeout_s=None):
    """在子进程里算对称差，返回 (result_dict | None, error | None)。

    【为什么必须隔离到子进程】FreeCAD 的布尔运算是 C++ 阻塞调用：Windows 上 signal.alarm
    不可用、线程也中断不了它。实测 PART-B（仅 912 面）单次 cut 跑过 840s
    未返回，此前代码靠 SKIP_BASE_NAMES 硬编码零件名规避——换装配体即失效，
    表现为整条流水线无限期卡住、无输出无报错。
    子进程方案实测开销很小：正常零件 exportBrep 0.011s、端到端 0.6s。

    返回的 result_dict 结构见 boolean_worker.py，含 removed_volume/added_volume/
    diff_volume/bbox/diff_bbox_size_mm。超时或失败时返回 (None, 原因字符串)。
    """
    budget = BOOLEAN_TIMEOUT_S if timeout_s is None else timeout_s
    workdir = tempfile.mkdtemp(prefix="geomdiff_bool_")
    path_old = os.path.join(workdir, "old.brep")
    path_new = os.path.join(workdir, "new.brep")
    path_out = os.path.join(workdir, "result.json")
    try:
        old_shape.exportBrep(path_old)
        new_shape.exportBrep(path_new)
    except Exception as exc:                       # noqa: BLE001
        shutil.rmtree(workdir, ignore_errors=True)
        return None, "brep_export_failed: %s" % exc

    fc_bin_arg = FC_BIN if sys.platform == "win32" else ""
    fc_lib_arg = FC_LIB if sys.platform == "win32" else ""
    proc = subprocess.Popen(
        [FC_PYTHON, BOOLEAN_WORKER, fc_bin_arg, fc_lib_arg, path_old, path_new, path_out],
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        text=True, encoding="utf-8", errors="replace")
    try:
        proc.communicate(timeout=budget)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.communicate()
        shutil.rmtree(workdir, ignore_errors=True)
        return None, "timeout"

    try:
        with open(path_out, "r", encoding="utf-8") as f:
            payload = json.load(f)
    except (OSError, ValueError):
        shutil.rmtree(workdir, ignore_errors=True)
        return None, "worker_crashed(rc=%s)" % proc.returncode
    finally:
        shutil.rmtree(workdir, ignore_errors=True)

    if not payload.get("ok"):
        return None, payload.get("error") or "worker_failed"
    return payload, None


def check_global_alignment(pair_shifts):
    """由【已配对零件的位移向量一致性】判断两版是否整体配准不一致。

    why：若新版 STP 整体被平移/旋转（不同人导出、基准变更），会对每一个零件都算出
    巨大差异，然后逐一做昂贵的布尔运算——既慢又全是伪差异。

    why 不用"两版全部几何的整体 bbox"（这是本轮实测推翻的第一版实现）：
    整体 bbox 会把"增删零件"误判成"整体平移"。实测本案例基线（已知配准良好）被判
    misaligned：中心位移 1.24mm、整体尺寸差 5.41%，根因是新版多了一个匿名对象
    `SOLID003`（X[-10.15,10.15]，超出旧版范围），bbox 因此被撑大——
    这是零件增删，不是坐标系变化。按 bbox 判会对每个正常的新旧 STP 对都误报。
    正确判据是"绝大多数已配对零件是否朝同一方向平移了同一距离"：
    整体坐标系变了 → 全部零件位移向量一致；只是增删/改了几个零件 → 位移零散且多为 0。

    pair_shifts: [(dx, dy, dz), ...] 每个已配对零件的中心位移向量。
    实测本案例 62 对全部 ≤0.2017mm 且中位数≈0 → 正确判为未misaligned。

    why 不自动纠正：自动对齐是另一个量级的功能，且会掩盖"整体位置确实改了"这种
    真实改动。这里只检测 + 告警 + 落进产物，让 PPT 向读者交代，
    否则读者看到几十处伪差异会以为都是真的。
    """
    if not pair_shifts:
        return {"checked": False, "reason": i18n.t("alignment.no_pairs")}

    mags = sorted(math.sqrt(dx * dx + dy * dy + dz * dz)
                  for dx, dy, dz in pair_shifts)
    n = len(mags)
    median_shift = mags[n // 2] if n % 2 else (mags[n // 2 - 1] + mags[n // 2]) / 2.0

    # 一致性：位移向量的平均向量长度 与 各自长度的平均值 之比。
    # 整体平移时两者几乎相等（比值≈1）；零散移动时平均向量相互抵消（比值≈0）。
    mean_vec = [sum(s[i] for s in pair_shifts) / n for i in range(3)]
    mean_vec_len = math.sqrt(sum(c * c for c in mean_vec))
    mean_mag = sum(mags) / n
    consistency = (mean_vec_len / mean_mag) if mean_mag > 1e-9 else 0.0

    # 只有"位移够大"【且】"方向高度一致"才判定整体配准问题——两个条件都必须满足，
    # 否则几个零件各自挪动就会被误报成整体坐标系变化。
    misaligned = (median_shift > GLOBAL_ALIGN_TOLERANCE_MM
                  and consistency > GLOBAL_ALIGN_CONSISTENCY)
    return {
        "checked": True,
        "misaligned": misaligned,
        "paired_count": n,
        "median_shift_mm": round(median_shift, 6),
        "max_shift_mm": round(mags[-1], 6),
        "mean_shift_vector": [round(c, 6) for c in mean_vec],
        "direction_consistency": round(consistency, 4),
    }




def run(bom_json_path, stp_old, stp_new, out_json, skip_parts=None,
        min_diff_pct=None, boolean_timeout=None, max_faces=None):
    with open(bom_json_path, "r", encoding="utf-8") as f:
        bom = json.load(f)

    skip_parts = set(skip_parts or ())
    pct_gate = VOLUME_THRESHOLD_PCT if min_diff_pct is None else min_diff_pct

    doc_old = FreeCAD.newDocument("Old")
    doc_new = FreeCAD.newDocument("New")

    t0 = _time.time()
    Import.insert(stp_old, doc_old.Name)
    print(f"Loaded old version: {_time.time()-t0:.1f}s ({len(doc_old.Objects)} objects)")
    t0 = _time.time()
    Import.insert(stp_new, doc_new.Name)
    print(f"Loaded new version: {_time.time()-t0:.1f}s ({len(doc_new.Objects)} objects)")

    old_map = build_label_map(doc_old)
    new_map = build_label_map(doc_new)

    results = []
    # 被跳过/被筛掉的零件必须显式记录：静默跳过会让读者把"没提到"理解成"没差异"
    skipped_parts = []
    filtered_by_threshold = []
    # 已配对零件的中心位移向量，供配准检查用（判据见 check_global_alignment）
    pair_shifts = []
    total = len(bom["candidates"])
    candidate_names = {c["base_name"] for c in bom["candidates"]}

    def _child_labels(objs):
        """取这些对象在装配树里的直接子对象 Label。

        why 需要它：STEP 导入会把顶层装配体建成一个 ``App::Part`` 容器，容器本身会作为
        BOM 候选进来。非容器（``Part::Feature``）没有 ``Group`` 属性，返回空表。
        """
        return [getattr(c, "Label", "") for o in objs
                for c in (getattr(o, "Group", None) or [])]

    for idx, cand in enumerate(bom["candidates"]):
        bn = cand["base_name"]
        print(f"[{idx+1}/{total}] {bn}...", end=" ", flush=True)

        if bn in skip_parts:
            skipped_parts.append({"name": bn, "reason": "user_skipped"})
            print("skipped (--skip-parts)")
            continue

        old_objs = old_map.get(bn, [])
        new_objs = new_map.get(bn, [])

        # 装配体容器（App::Part）不是零件：它的"形状"是全部子件的并集，于是**任何一个子件
        # 变化都会让容器也报一次差异**——那是重复计数，不是新信息，而且会把「2 处改动」
        # 显示成「3 处」。
        # 判据是「它装着本次参与比对的其它候选」：没有子件的空容器仍按普通零件处理，
        # 否则会把真实零件静默漏掉。命中时记入 skipped_parts，不静默丢弃。
        if old_objs and new_objs and any(
                base_name(k) in candidate_names
                for k in _child_labels(old_objs) + _child_labels(new_objs)):
            skipped_parts.append({"name": bn, "reason": "assembly_container"})
            print("skipped (assembly container)")
            continue

        entry = {
            "base_name": bn,
            "old_count": cand["old_count"],
            "new_count": cand["new_count"],
            "count_match": cand["count_match"],
            "geometric_changes": [],
        }

        if not cand["count_match"]:
            entry["note"] = "count mismatch"
            results.append(entry)
            print("count mismatch")
            continue

        if len(old_objs) == 0 or len(new_objs) == 0:
            entry["note"] = "no geometry match"
            results.append(entry)
            print("no geometry match")
            continue

        if cand["old_count"] == 1:
            pairs = [(old_objs[0], new_objs[0])]
            unpaired = []
        else:
            pairs, unpaired = pair_by_proximity(old_objs, new_objs)

        if unpaired:
            # 配对不上的旧对象：可能是同名部件在新版被重构（如Part::Feature变成了App::Part子装配，
            # 或体积差异过大导致联合评分排除），必须显式记录交人工复核，不能静默丢弃。
            entry["unpaired_old_labels"] = [o.Label for o in unpaired]

        t_part = _time.time()
        for i, (old_obj, new_obj) in enumerate(pairs):
            old_shape = get_shape(old_obj)
            new_shape = get_shape(new_obj)
            if old_shape is None or new_shape is None:
                skipped_parts.append({"name": bn, "instance_index": i,
                                      "reason": "no_shape"})
                continue

            kind, delta = classify_change(old_shape, new_shape)
            # 每个已配对零件的位移向量都要收集，配准检查靠它们的一致性判断
            # （不能只收 moved 的：整体平移时全部零件都会 moved，但只是"改了一个零件"
            # 的场景里大多数零件位移为 0，正是那些 0 让一致性判据能区分两种情形）。
            try:
                co = old_shape.BoundBox.Center
                cn = new_shape.BoundBox.Center
                pair_shifts.append((cn.x - co.x, cn.y - co.y, cn.z - co.z))
            except Exception:
                pass
            if kind == "identical":
                continue

            if kind == "moved":
                # 位移类：不做布尔运算。对称差在这里毫无意义——两形状零重叠时它等于
                # "整件被去掉 + 整件新增"，既昂贵又会把读者误导成零件被换掉了。
                bb_new = new_shape.BoundBox
                entry["geometric_changes"].append({
                    "instance_index": i,
                    "change_type": "moved",
                    "old_label": old_obj.Label,
                    "new_label": new_obj.Label,
                    "old_volume": old_shape.Volume,
                    "new_volume": new_shape.Volume,
                    "volume_delta": 0.0,
                    "volume_delta_pct": 0.0,
                    "translation_mm": round(delta["translation_mm"], 6),
                    "rotation_deg": round(delta["rotation_deg"], 4),
                    "rotation_detected_by": delta["rotation_detected_by"],
                    "old_center": list(old_shape.BoundBox.Center),
                    "new_center": list(bb_new.Center),
                    # 位移类没有对称差，红框框住零件自身 bbox。两版各记一份：
                    # 旧位置图要框旧 bbox、新位置图框新 bbox，否则框会画在零件旁边
                    # （实测：两图共用新 bbox 时，旧位置图上红框与品红零件明显错开）。
                    "bbox": get_bbox_dict(bb_new),
                    "bbox_old": get_bbox_dict(old_shape.BoundBox),
                    "diff_bbox_size_mm": [bb_new.XLength, bb_new.YLength, bb_new.ZLength],
                    "parent_chain": get_parent_chain(old_obj),
                })
                continue

            n_faces = max(face_count(old_shape), face_count(new_shape))
            if shape_too_complex(old_shape, max_faces) or shape_too_complex(new_shape, max_faces):
                skipped_parts.append({"name": bn, "instance_index": i,
                                      "reason": "too_complex", "faces": n_faces})
                entry["note"] = "too complex, skipped"
                continue

            # 布尔一律走子进程 + 硬超时：面数闸门拦不住所有病态零件（见 symmetric_diff_isolated）
            payload, err = symmetric_diff_isolated(old_shape, new_shape, boolean_timeout)
            if payload is None:
                skipped_parts.append({"name": bn, "instance_index": i,
                                      "reason": err, "faces": n_faces})
                entry["note"] = "boolean %s, skipped" % err
                print(f"[boolean {err}] ", end="", flush=True)
                continue

            vol = payload["diff_volume"]
            if vol < VOLUME_THRESHOLD:
                continue
            if pct_gate > 0 and new_shape.Volume > 0:
                pct = vol / new_shape.Volume * 100.0
                if pct < pct_gate:
                    filtered_by_threshold.append({
                        "name": bn, "instance_index": i,
                        "volume_delta": vol, "volume_delta_pct": round(pct, 4),
                        "threshold_pct": pct_gate})
                    continue

            bbox = payload["bbox"]
            entry["geometric_changes"].append({
                "instance_index": i,
                "change_type": "shape_changed",
                "old_label": old_obj.Label,
                "new_label": new_obj.Label,
                "old_volume": old_shape.Volume,
                "new_volume": new_shape.Volume,
                "volume_delta": vol,
                "volume_delta_pct": vol / new_shape.Volume * 100 if new_shape.Volume > 0 else None,
                "bbox": bbox,
                "diff_bbox_size_mm": payload["diff_bbox_size_mm"],
                "parent_chain": get_parent_chain(old_obj),
            })

        elapsed = _time.time() - t_part
        if entry["geometric_changes"] or entry.get("unpaired_old_labels"):
            results.append(entry)
            n_moved = sum(1 for c in entry["geometric_changes"]
                          if c.get("change_type") == "moved")
            print(f"{len(entry['geometric_changes'])} changes (moved={n_moved}), "
                  f"unpaired={entry.get('unpaired_old_labels', [])} ({elapsed:.1f}s)")
        else:
            print(f"no change ({elapsed:.1f}s)")

    # 配准前置检查：整体坐标系变了会让每个零件都算出伪差异。但也不自动纠正（见函数说明）。
    # 【为什么放在配对循环之后】判据需要"已配对零件的位移向量"，那是循环里才有的数据。
    # 用两版整体 bbox 提前判过一版，实测会把"新版多了个零件"误报成"整体平移"，已废弃。
    global_alignment = check_global_alignment(pair_shifts)
    if global_alignment.get("misaligned"):
        print("!! WARNING: global alignment mismatch between the two versions "
              f"(median paired-part shift {global_alignment['median_shift_mm']}mm, "
              "direction consistency "
              f"{global_alignment['direction_consistency']}). "
              "Many of the differences above may be spurious; recorded in the "
              "global_alignment field.")

    FreeCAD.closeDocument(doc_old.Name)
    FreeCAD.closeDocument(doc_new.Name)

    output = {
        "old_file": stp_old,
        "new_file": stp_new,
        "total_candidates": len(bom["candidates"]),
        "geometric_diffs": results,
        "diff_count": len(results),
        "global_alignment": global_alignment,
        "skipped_parts": skipped_parts,
        "filtered_by_threshold": filtered_by_threshold,
        "settings": {
            "max_faces_for_boolean": MAX_FACES_FOR_BOOLEAN if max_faces is None else max_faces,
            "boolean_timeout_s": BOOLEAN_TIMEOUT_S if boolean_timeout is None else boolean_timeout,
            "min_diff_pct": pct_gate,
            "skip_parts": sorted(skip_parts),
        },
    }

    with open(out_json, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)

    print(f"\nTotal candidates: {output['total_candidates']}")
    print(f"Geometric differences: {output['diff_count']}")
    for r in results:
        n = len(r["geometric_changes"])
        label = r.get("note", f"{n} change(s)")
        print(f"  {r['base_name']}: {label}")
    if skipped_parts:
        print(f"Skipped parts ({len(skipped_parts)}):")
        for s in skipped_parts:
            print(f"  {s['name']}: {s['reason']}" +
                  (f" (faces={s['faces']})" if s.get("faces") else ""))
    if filtered_by_threshold:
        print(f"Filtered out by relative threshold: {len(filtered_by_threshold)}")
    print(f"Wrote: {out_json}")


def main(argv=None):
    ap = argparse.ArgumentParser(description="Geometric-layer difference comparison")
    ap.add_argument("bom_json")
    ap.add_argument("stp_old")
    ap.add_argument("stp_new")
    ap.add_argument("out_json")
    ap.add_argument("--max-faces", type=int, default=MAX_FACES_FOR_BOOLEAN,
                    help="Skip boolean operations for parts with more faces than this "
                         "(cheap pre-gate)")
    ap.add_argument("--boolean-timeout", type=float, default=BOOLEAN_TIMEOUT_S,
                    help="Hard subprocess timeout for a single boolean operation (seconds)")
    ap.add_argument("--skip-parts", default="",
                    help="Comma-separated base_names to skip (no part name is hardcoded)")
    ap.add_argument("--min-diff-pct", type=float, default=VOLUME_THRESHOLD_PCT,
                    help="Diff volume must exceed this percentage of the part volume; "
                         "default 0 = disabled")
    ap.add_argument("--lang", default=None, choices=i18n.available_langs(),
                    help="Language of the generated report text (default: en)")
    args = ap.parse_args(argv)

    # 产出物语言必须在任何工作之前设定：check_global_alignment 的 reason 文案在 run() 里生成
    i18n.set_lang(args.lang)

    skip = [s.strip() for s in args.skip_parts.split(",") if s.strip()]
    run(args.bom_json, args.stp_old, args.stp_new, args.out_json,
        skip_parts=skip, min_diff_pct=args.min_diff_pct,
        boolean_timeout=args.boolean_timeout, max_faces=args.max_faces)


if __name__ == "__main__":
    main()
