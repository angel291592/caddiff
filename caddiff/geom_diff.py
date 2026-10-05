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


def build_label_map(doc, key_map=None):
    """按**比较 key** 归并对象。

    key 优先取 BOM 层算好的 ``key_map``（原始名 -> key）——那是单一真相源；本函数自己
    再算一遍就会与 BOM 层漂移（两层口径不一致 = 全部零件 "no geometry match"）。
    查不到时才回退 ``base_name``，这样没有 key_map 的旧版 bom_diff.json 仍可跑。
    """
    key_map = key_map or {}
    label_map = defaultdict(list)
    for obj in doc.Objects:
        if obj.TypeId in ("Part::Feature", "App::Part"):
            bn = key_map.get(obj.Label) or base_name(obj.Label)
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

    返回 4 元组：``(paired, unpaired_old, unpaired_new, no_shape_objs)``。
    未配对**必须两侧对称返回**：体积差 >50% 的零件本来就是形状大改的一处，把它从
    配对结果里筛掉却不告知，等于把真实改动静默丢弃（gate1 语料实测的假阴性，见坑 J3）。
    无形状（isNull）的对象不进配对池，但**必须随返回值带出**交给调用方记
    ``skipped_parts``——无声消失就是静默失败。

    双侧孤儿由调用方按最近中心 1:1 就地配对并**降级报差异**（不宣称"同一个零件"，
    只取双侧 bbox/体积）；孤儿对的位移不进全局配准判据。
    """
    old_features = [(o, get_shape(o)) for o in old_objs]
    no_shape_objs = [o for o, s in old_features if s is None]
    old_features = [(o, s) for o, s in old_features if s is not None]
    new_features = [(o, get_shape(o)) for o in new_objs]
    no_shape_objs += [o for o, s in new_features if s is None]
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
    unpaired_new = [new_obj for i, (new_obj, _) in enumerate(new_features)
                    if i not in used_new]
    return paired, unpaired_old, unpaired_new, no_shape_objs


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




def _pair_orphans(unpaired_old, unpaired_new):
    """双侧孤儿按最近中心 1:1 就地配对（无体积筛）。

    why 不套体积筛：孤儿正是被「体积差>50% 不可信」的筛从全族里剩下的，在孤儿之间
    再套同一个筛只会把两边全部剩空。就近配对的目的只是给差异条目配齐双侧 bbox/体积
    供渲染与报告显示，**不宣称两侧是「同一个物理零件」**——单侧落单时该侧字段置
    None，渲染层对缺失对象优雅降级（precompute_shapes 的 ``!!`` 分支）。
    返回的位移不得进全局配准判据（调用方保证）。
    """
    leftover_new = list(unpaired_new)
    out = []
    for o in unpaired_old:
        center = get_shape(o).BoundBox.Center
        if leftover_new:
            best = min(range(len(leftover_new)),
                       key=lambda j: center.distanceToPoint(
                           get_shape(leftover_new[j]).BoundBox.Center))
            out.append((o, leftover_new.pop(best)))
        else:
            out.append((o, None))
    out.extend((None, n) for n in leftover_new)
    return out


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

    old_map = build_label_map(doc_old, bom.get("old_key_map"))
    new_map = build_label_map(doc_new, bom.get("new_key_map"))

    results = []
    # 被跳过/被筛掉的零件必须显式记录：静默跳过会让读者把"没提到"理解成"没差异"
    skipped_parts = []
    filtered_by_threshold = []
    # 已配对零件的中心位移向量，供配准检查用（判据见 check_global_alignment）
    pair_shifts = []
    total = len(bom["candidates"])
    candidate_names = {c["base_name"] for c in bom["candidates"]}

    def _child_keys(objs, key_map):
        """取这些对象在装配树里的直接子对象的**比较 key**。

        why 需要它：STEP 导入会把顶层装配体建成一个 ``App::Part`` 容器，容器本身会作为
        BOM 候选进来。非容器（``Part::Feature``）没有 ``Group`` 属性，返回空表。

        why 返回 key 而不是 Label：候选集合 ``candidate_names`` 装的是 key，两边口径
        必须一致——精确匹配生效时对象的 key 与 Label 不是一回事（见 bom_diff.align_keys）。
        """
        key_map = key_map or {}
        return [key_map.get(getattr(c, "Label", "")) or base_name(getattr(c, "Label", ""))
                for o in objs for c in (getattr(o, "Group", None) or [])]

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
        # 判据是「它装着本次参与比对的**其它**候选」：没有子件的空容器仍按普通零件处理，
        # 否则会把真实零件静默漏掉。命中时记入 skipped_parts，不静默丢弃。
        # `k != bn` 是防御项：族名折叠生效时，容器的子对象 key 可能**等于容器自己的 bn**
        # （实测：8 个 `... v001` 零件折叠成 `... v` 后与容器同 key），那种自我命中会把
        # 整组真实零件一起吞掉，把「1 处位移」报成「无差异」（假阴性）。精确匹配生效时
        # 该条件恒真，不改变本判定对真容器的行为。
        if old_objs and new_objs and any(
                k in candidate_names and k != bn
                for k in (_child_keys(old_objs, bom.get("old_key_map"))
                          + _child_keys(new_objs, bom.get("new_key_map")))):
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

        no_shape_objs = []
        if cand["old_count"] == 1:
            pairs = [(old_objs[0], new_objs[0])]
            unpaired_old, unpaired_new = [], []
        else:
            pairs, unpaired_old, unpaired_new, no_shape_objs = pair_by_proximity(
                old_objs, new_objs)

        # 形状读不出的对象必须留痕（另一条静默通道的封堵，见坑 J3）
        for _ in no_shape_objs:
            skipped_parts.append({"name": bn, "reason": "no_shape"})
            print("no shape (pairing pool)")

        if unpaired_old:
            # 配对不上的旧对象：可能是同名部件在新版被重构（如Part::Feature变成了App::Part子装配，
            # 或体积差异过大导致联合评分排除），必须显式记录交人工复核，不能静默丢弃。
            entry["unpaired_old_labels"] = [o.Label for o in unpaired_old]
        if unpaired_new:
            entry["unpaired_new_labels"] = [o.Label for o in unpaired_new]

        # 孤儿＝配对器按「体积差>50% 不可信」筛掉的对象，正是形状大改的零件（坑 J3，
        # gate1 语料实测：6 件同名 SOLID 里 1 件体积减半被报成"无差异"）。双侧剩余件
        # 就近 1:1 配对（只取双侧 bbox/体积，不宣称"同一个零件"），每对降级报一条
        # shape_changed；单侧落单的孤儿各自单侧报告。位移不进 pair_shifts——配准判据
        # 不吃不确信的对。
        for oi, (old_o, new_o) in enumerate(_pair_orphans(unpaired_old, unpaired_new)):
            old_shape = get_shape(old_o) if old_o is not None else None
            new_shape = get_shape(new_o) if new_o is not None else None
            # render_diff 对 bbox / instance_index 是**直接下标**（:1185/:1776），
            # 每个条目都必须带全；instance_index 顺延在家族实例区内，不与已配对序号冲突。
            change_entry = {
                "instance_index": len(pairs) + oi,
                "change_type": "shape_changed",
                "old_label": old_o.Label if old_o is not None else None,
                "new_label": new_o.Label if new_o is not None else None,
                "old_volume": old_shape.Volume if old_shape is not None else None,
                "new_volume": new_shape.Volume if new_shape is not None else None,
                # 布尔没跑，这不是对称差；双侧数值只当"变化规模的证据"，单侧为 None。
                "volume_delta": (abs(old_shape.Volume - new_shape.Volume)
                                 if (old_shape is not None
                                     and new_shape is not None) else None),
                "volume_delta_pct": None,
                # render_diff 的 bbox 直接下标：永远取"存在侧"自身 bbox 兜底。
                "bbox": get_bbox_dict(
                    (new_shape if new_shape is not None else old_shape).BoundBox),
                "bbox_old": (get_bbox_dict(old_shape.BoundBox)
                             if old_shape is not None else None),
                "highlight_mode": "whole_part",
                "degraded_reason": "unpaired_after_proximity",
            }
            if old_o is not None:
                change_entry["parent_chain"] = get_parent_chain(old_o)
            entry["geometric_changes"].append(change_entry)
            print("[unpaired degraded] ", end="", flush=True)

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
                # 布尔算不出对称差。但 `classify_change` 判定 shape_changed 的前提就是
                # 「体积或 bbox 已超容差」——**体积差本身就是形状改变的充分证据**，不能
                # 因为量不出高亮范围就把这处差异丢掉。实测：真实公开装配体的零件被削掉
                # 10.4mm³ 时 OCCT 对两个近乎重合的形状返回退化结果，整处差异静默消失、
                # 退出码仍为 0（CI 闸门会放行真实改动）。
                # 降级：高亮**整个零件**（render 层按 highlight_mode=whole_part 处理），
                # 并把降级原因写进条目——粗，但不丢、不静默。
                # 只在体积差确实超容差时降级：纯 bbox 变化（如旋转）没有体积证据，
                # 那种情况仍按算不出来记录，不猜。
                vol_delta = abs(old_shape.Volume - new_shape.Volume)
                if err == "boolean_no_result" and vol_delta > VOLUME_TOLERANCE:
                    bb_new = new_shape.BoundBox
                    entry["geometric_changes"].append({
                        "instance_index": i,
                        "change_type": "shape_changed",
                        "old_label": old_obj.Label,
                        "new_label": new_obj.Label,
                        "old_volume": old_shape.Volume,
                        "new_volume": new_shape.Volume,
                        "volume_delta": vol_delta,
                        "volume_delta_pct": (vol_delta / new_shape.Volume * 100.0
                                             if new_shape.Volume > 0 else None),
                        "bbox": get_bbox_dict(bb_new),
                        "bbox_old": get_bbox_dict(old_shape.BoundBox),
                        "diff_bbox_size_mm": [bb_new.XLength, bb_new.YLength, bb_new.ZLength],
                        "highlight_mode": "whole_part",
                        "degraded_reason": err,
                        "parent_chain": get_parent_chain(old_obj),
                    })
                    print(f"[degraded {err}] ", end="", flush=True)
                    continue
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
        if (entry["geometric_changes"] or entry.get("unpaired_old_labels")
                or entry.get("unpaired_new_labels")):
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
