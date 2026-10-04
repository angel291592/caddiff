r"""子进程里执行布尔对称差，供 geom_diff.py 以硬超时方式调用。

【为什么必须放到子进程】FreeCAD 的布尔运算是 C++ 阻塞调用：Python 层的 signal.alarm 在
Windows 不可用，线程也无法中断它。而病态零件确实存在——实测 PART-B
（仅 912 面，远低于任何合理的面数闸门）单次 cut 跑过 840s 未返回。
只靠"面数事前拦截"防不住它：面数与布尔耗时不成正比，闸门定在哪都会漏。
把布尔放进子进程后，父进程 kill 它即可真正中断，超时零件被记为 skipped_parts 而非卡死。

【为什么不需要在子进程里重新导入 STP】Shape 可以 exportBrep 落盘、importBrep 读回，
成本只与单个零件相关。实测：正常零件 exportBrep 0.011s、子进程端到端 0.6s；
PART-B 那种 2MB 的 BREP 导出也只要 0.55s。

【必须先 import FreeCAD 再 import Part】直接 import Part 会以 0xC0000005 访问违例崩溃
（已实测 rc=3221225477）——Part 的二进制扩展依赖 FreeCAD 初始化过的全局状态。

用法（由 geom_diff.py 调用，不单独使用）：
    <FreeCAD>\bin\python.exe boolean_worker.py <fc_bin> <fc_lib> <old.brep> <new.brep> <out.json>
"""
import json
import os
import sys
import time


def main():
    if len(sys.argv) != 6:
        print("usage: boolean_worker.py <fc_bin> <fc_lib> <old.brep> <new.brep> <out.json>")
        return 2

    fc_bin, fc_lib, path_old, path_new, out_json = sys.argv[1:6]
    if fc_bin and os.path.isdir(fc_bin):
        sys.path.insert(0, fc_bin)
    if fc_lib and os.path.isdir(fc_lib):
        sys.path.insert(0, fc_lib)

    import FreeCAD  # noqa: F401  必须先于 Part，见模块说明
    import Part

    t0 = time.time()
    old_shape = Part.Shape()
    old_shape.importBrep(path_old)
    new_shape = Part.Shape()
    new_shape.importBrep(path_new)

    removed = old_shape.cut(new_shape)
    added = new_shape.cut(old_shape)
    diff = removed.fuse(added)
    bb = diff.BoundBox

    result = {
        "ok": True,
        "elapsed_s": round(time.time() - t0, 3),
        "removed_volume": removed.Volume,
        "added_volume": added.Volume,
        "diff_volume": diff.Volume,
        "bbox": {
            "x_min": bb.XMin, "y_min": bb.YMin, "z_min": bb.ZMin,
            "x_max": bb.XMax, "y_max": bb.YMax, "z_max": bb.ZMax,
            "center": [bb.Center.x, bb.Center.y, bb.Center.z],
        },
        "diff_bbox_size_mm": [bb.XLength, bb.YLength, bb.ZLength],
    }
    with open(out_json, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:                       # noqa: BLE001
        # 子进程里的任何失败都必须变成父进程能读到的结构化结果，
        # 否则父进程只看到非零 returncode，无法区分"几何算不出来"与"进程被 kill"。
        if len(sys.argv) == 6:
            try:
                with open(sys.argv[5], "w", encoding="utf-8") as f:
                    json.dump({"ok": False, "error": "%s: %s"
                               % (type(exc).__name__, exc)}, f, ensure_ascii=False)
            except OSError:
                pass
        sys.exit(1)
