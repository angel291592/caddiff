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
from collections import Counter

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
    """
    last_alpha = max((i for i, c in enumerate(name) if c.isalpha()), default=len(name) - 1)
    return name[: last_alpha + 1]


def diff_bom(old_path, new_path):
    old_names = extract_product_names(old_path)
    new_names = extract_product_names(new_path)

    old_counts = Counter(base_name(n) for n in old_names)
    new_counts = Counter(base_name(n) for n in new_names)

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
