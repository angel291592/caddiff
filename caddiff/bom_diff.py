"""
STEP装配体BOM层差异对比（PoC，验证：不用几何运算，能否从PRODUCT命名规律
可靠算出"增/删/候选变化"清单）。

用普通系统Python运行，不依赖FreeCAD：
    python bom_diff.py <stp_old> <stp_new> <out_json>
"""
import json
import os
import re
import sys
from collections import Counter, defaultdict

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
if SCRIPT_DIR not in sys.path:
    sys.path.insert(0, SCRIPT_DIR)

import console  # noqa: E402

console.enable_utf8_output()


PRODUCT_RE = re.compile(r"PRODUCT\('([^']*)'")


def extract_product_names(stp_path):
    with open(stp_path, "r", encoding="utf-8", errors="ignore") as f:
        text = f.read()
    return PRODUCT_RE.findall(text)


def base_name(name):
    """Creo每次导出会在零件名末尾追加新的实例编号段（下划线+数字）。
    截掉末尾从最后一个字母字符往后的所有内容，得到跨版本可比较的基础名。

    ⚠️ 它只提供**族名**，不单独决定是否折叠——单看名字区分不了「同一零件的实例编号」
    与「同前缀的不同零件」。是否真的折叠由 ``align_keys`` 按两版联合信息逐族决定。
    """
    last_alpha = max((i for i, c in enumerate(name) if c.isalpha()), default=len(name) - 1)
    return name[: last_alpha + 1]


def align_keys(old_names, new_names):
    """把两版 PRODUCT 名对齐成可比较的 key。返回 (old_key, new_key) 两个 dict。

    【why 必须按族判定】实测（openDogV3 真实公开装配体，8 个实体零件）：名字形如
    ``openDog V3_internals_toleranced v001/v002/.../v11``，`base_name` 把 8 个**不同零件**
    折叠成同一个 key；几何层的容器判定（子对象 key 命中候选集合即判为容器）于是**自我命中**，
    整组连同容器一起被跳过——1 处真实的 5mm 位移被报成「无差异」、退出码 0。
    那是 CI 闸门最危险的假阴性：闸门放行了本该拦下的改动。

    【规则】以 `base_name` 划分族，**逐族**决定比较用 key：
      - 该族在两版的名字集合完全相同 -> 用**精确名**（名字稳定时零歧义，不折叠）
      - 否则 -> 用**族名**（保留 Creo 场景：实例编号漂移、增删件仍能配上）

    【why 不能逐名判定】``BOLT_02/BOLT_03`` 走精确、``BOLT_04`` 走折叠，会让族名 ``BOLT``
    只在单边出现，凭空造出 added——见 tests/unit/test_bom_diff.py 的增删件用例。
    """
    old_fam, new_fam = defaultdict(list), defaultdict(list)
    for n in old_names:
        old_fam[base_name(n)].append(n)
    for n in new_names:
        new_fam[base_name(n)].append(n)

    old_key, new_key = {}, {}
    for fam in set(old_fam) | set(new_fam):
        o_names = set(old_fam.get(fam, ()))
        n_names = set(new_fam.get(fam, ()))
        stable = bool(o_names) and o_names == n_names
        for n in o_names:
            old_key[n] = n if stable else fam
        for n in n_names:
            new_key[n] = n if stable else fam
    return old_key, new_key


def diff_bom(old_path, new_path):
    old_names = extract_product_names(old_path)
    new_names = extract_product_names(new_path)

    # key 由两版联合决定（见 align_keys）。它同时写进产物，供几何层复用同一套 key——
    # 几何层若自己再算一遍，就是两份真相源，必然漂移。
    old_key, new_key = align_keys(old_names, new_names)

    old_counts = Counter(old_key[n] for n in old_names)
    new_counts = Counter(new_key[n] for n in new_names)

    removed = sorted(set(old_counts) - set(new_counts))
    added = sorted(set(new_counts) - set(old_counts))
    common = sorted(set(old_counts) & set(new_counts))

    candidates = []
    for b in common:
        candidates.append(
            {
                "base_name": b,
                "old_count": old_counts[b],
                "new_count": new_counts[b],
                "count_match": old_counts[b] == new_counts[b],
            }
        )

    return {
        "old_file": old_path,
        "new_file": new_path,
        "old_total_products": len(old_names),
        "new_total_products": len(new_names),
        "removed": removed,
        "added": added,
        "candidates": candidates,
        # 原始名 -> 比较用 key。几何层按 Label 查这张表，保证两层用的是同一套 key。
        "old_key_map": old_key,
        "new_key_map": new_key,
    }


if __name__ == "__main__":
    if len(sys.argv) != 4:
        print("usage: python bom_diff.py <stp_old> <stp_new> <out_json>")
        sys.exit(1)

    result = diff_bom(sys.argv[1], sys.argv[2])

    with open(sys.argv[3], "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)

    print(f"Old version parts (before merging): {result['old_total_products']}")
    print(f"New version parts (before merging): {result['new_total_products']}")
    print(f"REMOVED (old only): {len(result['removed'])} -> {result['removed']}")
    print(f"ADDED   (new only): {len(result['added'])} -> {result['added']}")
    print(f"CANDIDATE (in both, sent to the geometric diff): {len(result['candidates'])}")
    count_mismatch = [c for c in result["candidates"] if not c["count_match"]]
    print(f"  of which instance count differs (a difference in itself): "
          f"{len(count_mismatch)} -> {[c['base_name'] for c in count_mismatch]}")
    print(f"Wrote: {sys.argv[3]}")
