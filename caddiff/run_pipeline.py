r"""STP 装配体差异对比流水线 —— 编排层。

对外入口是 ``caddiff diff``（见 cli.py），本模块也可独立执行：

    python run_pipeline.py <stp_old> <stp_new> <output_dir> [选项]

【为什么 step 2/3 用不同解释器】geom_diff / render_diff 依赖 FreeCAD 的编译扩展，
只兼容其打包绑定的 Python；bom_diff / build_pptx 是纯逻辑，用系统 Python。

【退出码契约】见 AGENTS.md §3：``0`` 无差异 / ``1`` 检出差异 / ``2`` 执行失败。
本模块只把失败**抛成 PipelineError**，由入口翻译成退出码。早先这里直接 ``sys.exit(1)``，
导致「子进程超时」与「检出差异」在调用方看来无法区分——CI 闸门据此判断必然出错。

【产物布局】见 AGENTS.md §3 与 docs/pipeline.md：
``<out>/diff_manifest.json`` · ``report.html`` · ``report.md`` · ``images/*.png`` ·
``compare.pptx``（仅 --pptx）· ``bom_diff.json`` · ``geom_diff.json``
"""
import argparse
import json
import os
import subprocess
import sys
import time

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
if SCRIPT_DIR not in sys.path:
    sys.path.insert(0, SCRIPT_DIR)

import console  # noqa: E402
import fcenv  # noqa: E402
import i18n  # noqa: E402
import report  # noqa: E402

console.enable_utf8_output()

SYSTEM_PYTHON = sys.executable

# FreeCAD 解释器延迟到真正要跑几何/渲染时才解析：`--help` 不该因为本机没装 FreeCAD 而失败。
FC_PYTHON = None

EXIT_OK = 0
EXIT_DIFF = 1
EXIT_ERROR = 2

# 本文件的产物版本号：diff_manifest.json 是机器契约，新增字段不算破坏性变更，
# 但调用方需要能判断自己拿到的是哪一版结构。
MANIFEST_VERSION = 1

# build_pptx 用 exit 2 表示「上游没产出 manifest」，与通用失败(1)区分开。
EXIT_MISSING_MANIFEST = 2


class PipelineError(RuntimeError):
    """流水线执行失败。由入口翻译成退出码 2（区别于「检出差异」的退出码 1）。"""


def _fc_python():
    global FC_PYTHON
    if FC_PYTHON is None:
        try:
            FC_PYTHON = fcenv.freecad_python()
        except RuntimeError as exc:
            raise PipelineError(str(exc)) from exc
    return FC_PYTHON


def run_step(name, cmd, timeout=600, artifacts=None):
    """跑一个子进程步骤。失败/超时时打印已产出的中间产物路径，便于人工接续排查。

    why 抛异常而不 sys.exit：退出码是入口的职责。这里若直接退出，调用方无法区分
    「上游工具崩了」（应得退出码 2）与「两版真的有差异」（应得退出码 1）。
    """
    print(f"\n{'=' * 60}")
    print(f"  [{name}]")
    print(f"{'=' * 60}")
    print(f"  CMD: {' '.join(cmd)}")
    t0 = time.time()
    try:
        result = subprocess.run(cmd, capture_output=False, timeout=timeout,
                                cwd=SCRIPT_DIR, shell=False)
    except subprocess.TimeoutExpired as exc:
        print(f"  TIMEOUT (>{timeout}s): step did not finish.")
        _print_artifacts(artifacts)
        raise PipelineError(
            f"step '{name}' timed out after {timeout}s") from exc
    except FileNotFoundError as exc:
        print(f"  NOT FOUND: {cmd[0]}")
        _print_artifacts(artifacts)
        raise PipelineError(f"interpreter or script not found: {cmd[0]}") from exc
    elapsed = time.time() - t0
    if result.returncode != 0:
        print(f"  FAILED (exit={result.returncode}, {elapsed:.0f}s)")
        if result.returncode == EXIT_MISSING_MANIFEST:
            print("  (upstream produced no manifest — check the render step log)")
        _print_artifacts(artifacts)
        raise PipelineError(
            f"step '{name}' failed with exit code {result.returncode}")
    print(f"  OK ({elapsed:.0f}s)")


def _print_artifacts(artifacts):
    existing = [p for p in (artifacts or []) if p and os.path.exists(p)]
    if not existing:
        return
    print("  Intermediate artifacts produced so far:")
    for p in existing:
        print(f"    {p}")


def estimate_render_timeout(geom_json, base=180, per_diff=60):
    """按实际差异数估算渲染超时，而不是写死一个数。

    渲染成本 ∝ 差异数（每处差异要扫 26 个候选视角 + 出 3 张图），
    写死 900s 在差异多时必然超时，而超时就意味着这一步的产物全丢。
    """
    try:
        with open(geom_json, "r", encoding="utf-8") as f:
            geom = json.load(f)
        n = sum(len(d.get("geometric_changes") or [])
                for d in geom.get("geometric_diffs") or [])
    except (OSError, ValueError):
        return base + per_diff * 5
    return int(base + per_diff * max(n, 1))


def build_summary(bom_json, geom_json, render_manifest, label_old="", label_new=""):
    """把散落在多个 JSON 里的结论汇总成一份摘要，供调用方（CI / agent / LLM）直接消费。

    why：此前 diff_manifest.json 只有各产物的路径，调用方要回答「有没有差异、几处、
    跳过了什么」必须再打开 2~3 个文件自己拼。对 LLM 调用尤其不友好——读一个文件就能
    生成回复，比让它自己去关联三个文件可靠得多。

    why 要把 BOM 层差异算进来：``geom_diff`` 只处理「两版都有的同名零件」，**纯增件 /
    纯删件根本进不了几何对比**。早先的摘要只数几何差异，于是「整个装配体被换掉」
    会呈现为「0 处差异」——这是最危险的一类假阴性，CI 闸门据此会放行错误提交。

    这里只做汇总，不重新计算任何结论：全部字段都取自上游产物，避免「摘要与明细不一致」。
    """
    summary = {
        "manifest_version": MANIFEST_VERSION,
        "diff_count": 0,
        "by_change_type": {},
        "parts_with_diff": [],
        "skipped_parts": [],
        "filtered_by_threshold": [],
        "unresolved_notes": [],
        "global_alignment_warning": False,
        "settings": {},
        "label_old": label_old or "",
        "label_new": label_new or "",
        # —— BOM 层（增件 / 删件 / 数量不一致）——
        "bom_added": [],
        "bom_removed": [],
        "bom_count_mismatch": [],
        "bom_diff_count": 0,
        # —— 判定字段：退出码与 CI 闸门只认这一个 ——
        "geometry_diff_count": 0,
        "has_differences": False,
    }

    try:
        with open(bom_json, "r", encoding="utf-8") as f:
            bom = json.load(f)
    except (OSError, ValueError):
        bom = {}
    summary["bom_added"] = sorted(bom.get("added") or [])
    summary["bom_removed"] = sorted(bom.get("removed") or [])
    summary["bom_count_mismatch"] = sorted(
        c.get("base_name") for c in (bom.get("candidates") or [])
        if c.get("base_name") and not c.get("count_match"))
    summary["bom_diff_count"] = (len(summary["bom_added"])
                                + len(summary["bom_removed"])
                                + len(summary["bom_count_mismatch"]))

    try:
        with open(render_manifest, "r", encoding="utf-8") as f:
            manifest = json.load(f)
    except (OSError, ValueError):
        manifest = []

    summary["diff_count"] = len(manifest)
    summary["geometry_diff_count"] = len(manifest)
    for e in manifest:
        ctype = e.get("change_type") or "shape_changed"
        summary["by_change_type"][ctype] = summary["by_change_type"].get(ctype, 0) + 1
        item = {"base_name": e.get("base_name"), "change_type": ctype}
        if ctype == "moved":
            item["translation_mm"] = e.get("translation_mm")
            item["rotation_deg"] = e.get("rotation_deg")
        else:
            item["volume_delta"] = e.get("volume_delta")
            item["volume_delta_pct"] = e.get("volume_delta_pct")
            item["removed_volume"] = e.get("removed_volume")
            item["added_volume"] = e.get("added_volume")
            item["cluster_count"] = e.get("cluster_count")
        summary["parts_with_diff"].append(item)

    try:
        with open(geom_json, "r", encoding="utf-8") as f:
            geom = json.load(f)
    except (OSError, ValueError):
        geom = {}

    # 诚实性字段：被跳过 / 筛掉 / 没比上的零件必须让调用方看得见，
    # 否则「没提到」会被读成「没差异」。
    summary["skipped_parts"] = geom.get("skipped_parts") or []
    summary["filtered_by_threshold"] = geom.get("filtered_by_threshold") or []
    summary["unresolved_notes"] = [
        {"base_name": d.get("base_name"), "note": d.get("note")}
        for d in (geom.get("geometric_diffs") or []) if d.get("note")]
    ga = geom.get("global_alignment") or {}
    summary["global_alignment_warning"] = bool(ga.get("misaligned"))
    summary["global_alignment"] = ga
    summary["total_candidates"] = geom.get("total_candidates")
    summary["settings"] = geom.get("settings") or {}

    summary["has_differences"] = bool(
        summary["bom_diff_count"] or summary["geometry_diff_count"])
    return summary


def run_diff(stp_old, stp_new, output_dir,
             label_old=None, label_new=None, lang=None,
             max_faces=None, boolean_timeout=None, skip_parts=None,
             min_diff_pct=None, export_pptx=False):
    """跑完整流水线，返回 diff_manifest 字典；失败抛 PipelineError。

    返回而不打印结论：退出码由调用方（cli.py）决定，本函数不 ``sys.exit``。
    """
    if lang:
        i18n.set_lang(lang)

    stp_old = os.path.abspath(stp_old)
    stp_new = os.path.abspath(stp_new)
    output_dir = os.path.abspath(output_dir)

    for p in (stp_old, stp_new):
        if not os.path.exists(p):
            raise PipelineError(f"input file not found: {p}")
    if os.path.isdir(stp_old) or os.path.isdir(stp_new):
        raise PipelineError("input must be STEP/STP files, not directories")

    os.makedirs(output_dir, exist_ok=True)

    bom_json = os.path.join(output_dir, "bom_diff.json")
    geom_json = os.path.join(output_dir, "geom_diff.json")
    image_dir = os.path.join(output_dir, "images")
    render_manifest = os.path.join(image_dir, "render_manifest.json")
    pptx_out = os.path.join(output_dir, "compare.pptx")
    manifest_path = os.path.join(output_dir, "diff_manifest.json")
    artifacts = [bom_json, geom_json, render_manifest]

    # Step 1: BOM diff（系统 Python，纯文本解析，不需要 FreeCAD）
    run_step("Step 1/5: BOM diff",
             [SYSTEM_PYTHON, os.path.join(SCRIPT_DIR, "bom_diff.py"),
              stp_old, stp_new, bom_json],
             artifacts=artifacts)

    # Step 2: 几何 diff（FreeCAD Python）
    geom_cmd = [_fc_python(), os.path.join(SCRIPT_DIR, "geom_diff.py"),
                bom_json, stp_old, stp_new, geom_json]
    if max_faces is not None:
        geom_cmd += ["--max-faces", str(max_faces)]
    if boolean_timeout is not None:
        geom_cmd += ["--boolean-timeout", str(boolean_timeout)]
    if skip_parts:
        geom_cmd += ["--skip-parts", skip_parts]
    if min_diff_pct is not None:
        geom_cmd += ["--min-diff-pct", str(min_diff_pct)]
    geom_cmd += ["--lang", i18n.get_lang()]
    run_step("Step 2/5: geometric diff", geom_cmd, timeout=1800, artifacts=artifacts)

    # Step 3: 渲染截图（FreeCAD Python，需要 GUI / 虚拟显示）
    # render_diff 需要新旧两个 STP：旧版用于现算差异几何体做颜色高亮，并渲染旧版同角度并排图。
    render_cmd = [_fc_python(), os.path.join(SCRIPT_DIR, "render_diff.py"),
                  geom_json, stp_old, stp_new, image_dir,
                  "--lang", i18n.get_lang()]
    if label_old:
        render_cmd += ["--label-old", label_old]
    if label_new:
        render_cmd += ["--label-new", label_new]
    run_step("Step 3/5: render diff images", render_cmd,
             timeout=estimate_render_timeout(geom_json), artifacts=artifacts)

    # Step 4: 汇总 manifest + 报告（纯标准库，直接在本进程调用，不必起子进程）
    # 先写 diff_manifest.json（PPT 的汇总页要读它拿 skipped / unresolved / alignment），再生成 PPT。
    summary = build_summary(bom_json, geom_json, render_manifest,
                            label_old or "", label_new or "")
    manifest = {
        "manifest_version": MANIFEST_VERSION,
        "stp_old": stp_old,
        "stp_new": stp_new,
        "label_old": label_old,
        "label_new": label_new,
        "lang": i18n.get_lang(),
        "bom_diff": bom_json,
        "geom_diff": geom_json,
        "render_manifest": render_manifest,
        "images_dir": image_dir,
        "pptx": pptx_out if export_pptx else None,
        "summary": summary,
    }
    with open(manifest_path, "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2)

    # 报告必须在 manifest 之后写：报告的唯一输入就是 manifest（避免两套口径）。
    print(f"\n{'=' * 60}")
    print("  [Step 4/5: report]")
    print(f"{'=' * 60}")
    report_paths = report.generate(manifest_path, lang=i18n.get_lang())
    manifest["report_html"], manifest["report_md"] = report_paths
    # 报告路径也要落盘：调用方（CI / PR 评论）需要知道去哪儿拿 HTML。
    with open(manifest_path, "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2)

    if export_pptx:
        run_step("Step 5/5: build PPTX",
                 [SYSTEM_PYTHON, os.path.join(SCRIPT_DIR, "build_pptx.py"),
                  render_manifest, pptx_out, "--lang", i18n.get_lang()],
                 artifacts=artifacts)

    print(f"\n{'=' * 60}")
    print("  Pipeline finished")
    print(f"{'=' * 60}")
    print(f"  Output dir: {output_dir}")
    print(f"  Manifest  : {manifest_path}")
    if export_pptx:
        print(f"  PPTX      : {pptx_out}")
    if summary["bom_diff_count"]:
        print(f"  BOM diffs : {summary['bom_diff_count']} "
              f"(added={len(summary['bom_added'])}, removed={len(summary['bom_removed'])}, "
              f"count_mismatch={len(summary['bom_count_mismatch'])})")
    if summary["geometry_diff_count"]:
        types = ", ".join(f"{k}={v}" for k, v in summary["by_change_type"].items())
        print(f"  Geometry  : {summary['geometry_diff_count']} difference(s) ({types})")
    if not summary["has_differences"]:
        print("  Result    : no differences detected between the two versions")
    if summary["skipped_parts"]:
        print(f"  NOTE: {len(summary['skipped_parts'])} part(s) were not compared "
              "— see summary.skipped_parts")
    if summary["global_alignment_warning"]:
        print("  NOTE: global alignment looks inconsistent — differences may contain "
              "many false positives")
    return manifest


def main(argv=None):
    """独立执行入口（等价于 ``caddiff diff``）。返回退出码。"""
    ap = argparse.ArgumentParser(
        description="Compare two STEP/STP assemblies and report what changed")
    ap.add_argument("stp_old")
    ap.add_argument("stp_new")
    ap.add_argument("output_dir")
    ap.add_argument("--label-old", default=None,
                    help="Label for the old version (default: derived from filename)")
    ap.add_argument("--label-new", default=None,
                    help="Label for the new version (default: derived from filename)")
    ap.add_argument("--lang", default=None, choices=i18n.available_langs(),
                    help="Language of the generated report text "
                         "(default: $CADDIFF_LANG, else en)")
    ap.add_argument("--pptx", action="store_true",
                    help="Also export the comparison as a PowerPoint deck")
    ap.add_argument("--max-faces", type=int, default=None,
                    help="Skip boolean ops for parts with more faces than this")
    ap.add_argument("--boolean-timeout", type=float, default=None,
                    help="Hard timeout (seconds) for a single boolean op")
    ap.add_argument("--skip-parts", default=None,
                    help="Comma-separated base_names to skip")
    ap.add_argument("--min-diff-pct", type=float, default=None,
                    help="Only report diffs larger than this %% of part volume")
    args = ap.parse_args(argv)

    try:
        manifest = run_diff(
            args.stp_old, args.stp_new, args.output_dir,
            label_old=args.label_old, label_new=args.label_new, lang=args.lang,
            max_faces=args.max_faces, boolean_timeout=args.boolean_timeout,
            skip_parts=args.skip_parts, min_diff_pct=args.min_diff_pct,
            export_pptx=args.pptx)
    except PipelineError as exc:
        print(f"\nERROR: {exc}", file=sys.stderr)
        return EXIT_ERROR
    return EXIT_DIFF if manifest["summary"]["has_differences"] else EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
