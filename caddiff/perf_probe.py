r"""性能快照探针 —— 只在 FreeCAD 解释器的**独立子进程**里跑，供 run_pipeline 以硬超时调用。

【为什么必须放独立子进程】和布尔运算是同一个理由（见 boolean_worker.py）：「没有求解器」
不等于「查质量属性便宜」。逐对象 Volume+质心实测 19–45 ms（坑 F9），而病态 Shape 上
``MatrixOfInertia`` 这类调用可能卡死——放进子进程，父进程才能 kill 它。也正因为如此，
本探针**不 import 几何 diff 的任何东西**：性能取数与形状 diff 必须在两个进程里。

【为什么两版各开一个临时文档】``Import.insert`` 会往文档里塞对象树，两版塞同一个文档
既分不清 old/new，也让 ``doc.Objects`` 翻倍。每版：新建文档 → insert → snapshot → 关文档。

用法（由 run_pipeline.py 调用，不单独使用）：
    <FreeCAD>/bin/python.exe perf_probe.py <stp_old> <stp_new> <out_json> [--budget S]

失败即非零退出，由父进程的 ``run_step`` 转成 PipelineError → 退出码 2（既有契约，不新增语义）。
"""
import argparse
import json
import os
import sys
import time

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
if SCRIPT_DIR not in sys.path:
    sys.path.insert(0, SCRIPT_DIR)

import console  # noqa: E402
import fcenv  # noqa: E402  （路径解析的唯一真相源，必须先于 import FreeCAD）

console.enable_utf8_output()

fcenv.inject_freecad_paths()

import FreeCAD  # noqa: E402
import Import  # noqa: E402

import perf_snapshot  # noqa: E402


def snapshot_side(path, side, budget_s):
    """导入一版 STP → 取快照 → 关掉文档（关之前必须取完：关文档会释放 Shape）。"""
    started = time.time()
    doc_name = "PerfProbe_" + side
    print("  [%s] loading %s" % (side, os.path.basename(path)))
    doc = FreeCAD.newDocument(doc_name)
    try:
        Import.insert(os.path.abspath(path), doc.Name)
        snapshot = perf_snapshot.snapshot(doc.Objects, side, budget_s=budget_s)
    finally:
        FreeCAD.closeDocument(doc.Name)
    print("  [%s] %d closed solid(s), %d not counted, %.1fs (import+snapshot)"
          % (side, snapshot["n_objects"],
             int(snapshot["n_zero_volume"]) + len(snapshot["unavailable"]),
             time.time() - started))
    return snapshot


def main(argv=None):
    ap = argparse.ArgumentParser(
        description="Take volume-based mass-property snapshots of two STEP revisions")
    ap.add_argument("stp_old")
    ap.add_argument("stp_new")
    ap.add_argument("out_json")
    ap.add_argument("--budget", type=float, default=perf_snapshot.DEFAULT_BUDGET_S,
                    metavar="S",
                    help="time budget in seconds per revision (default: %(default)s); "
                         "objects not reached are reported as not counted")
    args = ap.parse_args(argv)

    for path in (args.stp_old, args.stp_new):
        if not os.path.isfile(path):
            print("ERROR: input file not found: %s" % path, file=sys.stderr)
            return 2

    payload = {
        "old": snapshot_side(args.stp_old, "old", args.budget),
        "new": snapshot_side(args.stp_new, "new", args.budget),
    }

    out_dir = os.path.dirname(os.path.abspath(args.out_json))
    if out_dir and not os.path.isdir(out_dir):
        os.makedirs(out_dir, exist_ok=True)
    with open(args.out_json, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    print("  written: %s" % args.out_json)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except SystemExit:
        raise
    except Exception:                                     # noqa: BLE001
        # 子进程里任何失败都必须以非零退出让父进程看见（父进程据此给退出码 2），
        # 同时把 traceback 留在 stderr 便于排查是哪种失败。
        import traceback
        traceback.print_exc()
        print("\nERROR: performance probe failed (see traceback above)", file=sys.stderr)
        sys.exit(1)
