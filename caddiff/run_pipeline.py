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
import perf_diff  # noqa: E402  （纯逻辑：快照 → 性能差异；不在 FreeCAD 进程里跑）
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
    except OSError as exc:
        # FileNotFoundError 之外还有一批「根本启动不了」的形态：Windows 上不可执行的文件
        # 是 OSError(WinError 193) / PermissionError(5)，Linux 上是 PermissionError /
        # Exec format error。它们若冒泡出 main()，进程会以未捕获异常结束 → **退出码 1**，
        # 也就是「检出差异」——直接违反 AGENTS.md §3.1（内部失败必须传导到 2）。
        # 实测复现：cmd[0] 指向一个不可执行的路径时，真实 CLI 进程退出码为 1。
        print(f"  CANNOT START: {cmd[0]} ({type(exc).__name__})")
        _print_artifacts(artifacts)
        raise PipelineError(f"cannot start '{cmd[0]}': {exc}") from exc
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


def build_summary(bom_json, geom_json, render_manifest, label_old="", label_new="",
                  perf_json=None):
    """把散落在多个 JSON 里的结论汇总成一份摘要，供调用方（CI / agent / LLM）直接消费。

    why：此前 diff_manifest.json 只有各产物的路径，调用方要回答「有没有差异、几处、
    跳过了什么」必须再打开 2~3 个文件自己拼。对 LLM 调用尤其不友好——读一个文件就能
    生成回复，比让它自己去关联三个文件可靠得多。

    why 要把 BOM 层差异算进来：``geom_diff`` 只处理「两版都有的同名零件」，**纯增件 /
    纯删件根本进不了几何对比**。早先的摘要只数几何差异，于是「整个装配体被换掉」
    会呈现为「0 处差异」——这是最危险的一类假阴性，CI 闸门据此会放行错误提交。

    这里只做汇总，不重新计算任何结论：全部字段都取自上游产物，避免「摘要与明细不一致」。

    ``perf_json`` 给了才写 ``summary["performance_diff"]``（``--perf`` 关闭时传 None，
    摘要与产物都零变化）。性能差异**不参与** ``has_differences`` —— 它永不翻转退出码。
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
            # 降级标记（坑 J2）必须进 summary：布尔算不出对称差时高亮的是整个零件，
            # 读者若不知道就会把"整件都变了"当成真实结论。只在真的降级时才出现。
            if e.get("degraded_reason"):
                item["degraded_reason"] = e["degraded_reason"]
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
    if not summary["has_differences"]:
        # 无差异的原因必须进契约：summary 块对调用方承诺"读它就够"（docs/pipeline.md §9），
        # 新增可选字段允许不改版本号（AGENTS.md §3.2）。几何空但 BOM 有差异时不写——
        # 那是 has_differences=True 的场景，写"无几何差异"会误导 JSON 消费者误读结论。
        summary["reason"] = i18n.t("summary.no_geometry_diff")

    if perf_json is not None:
        # 性能块的**全部**内容整体透传（含每版绝对值）：报告的唯一输入是 manifest
        # （AGENTS.md §3.4 单一入口），少了绝对值报告就只能去读第二份文件、于是两套口径。
        # 读不出来时写空 dict：键存在（说明本次开了 --perf）但没有数字，不静默假装没开。
        try:
            with open(perf_json, "r", encoding="utf-8") as f:
                perf = json.load(f)
        except (OSError, ValueError):
            perf = {}
        summary["performance_diff"] = perf
    return summary


def run_diff(stp_old, stp_new, output_dir,
             label_old=None, label_new=None, lang=None,
             max_faces=None, boolean_timeout=None, skip_parts=None,
             min_diff_pct=None, render_timeout=None, export_pptx=False,
             perf=False, perf_tol=None, perf_budget=None, density=None):
    """跑完整流水线，返回 diff_manifest 字典；失败抛 PipelineError。

    返回而不打印结论：退出码由调用方（cli.py）决定，本函数不 ``sys.exit``。

    ``perf`` 打开时多一步「性能快照 + 性能差异」（Step 3/6），产物是
    ``<out>/perf_snapshot.json`` 与 ``<out>/perf_diff.json``，并写进 summary 的
    ``performance_diff`` 键。它**不参与**退出码判定：性能数字永不翻转 0→1。
    """
    if lang:
        i18n.set_lang(lang)
    # 校验先于「这些参数会被忽略」的提示：否则 `--perf-budget 0` 会先说"忽略它"、紧接着又
    # 因为它退出码 2——两句话互相矛盾。
    if perf_tol is not None and perf_tol < 0.0:
        raise PipelineError("--perf-tol must be non-negative")
    if perf_budget is not None and perf_budget <= 0.0:
        raise PipelineError("--perf-budget must be positive")
    if not perf and (perf_tol is not None or perf_budget is not None
                     or density is not None):
        print("  NOTE: --perf-tol / --perf-budget / --density only apply together "
              "with --perf — ignoring them")
    if density is not None and density > perf_diff.MAX_PLAUSIBLE_DENSITY_KG_PER_MM3:
        # 不拒绝（取值约定见 docs/performance.md §1），但一个比锇还致密 1e6 倍的密度
        # 不该悄悄算出一个 kg 数字。
        print("  NOTE: --density %.3g kg/mm3 is denser than any known material "
              "(osmium ≈ 2.3e-05) — did you mean g/cm3?" % density)

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
    perf_snapshot_json = os.path.join(output_dir, "perf_snapshot.json")
    perf_json = os.path.join(output_dir, "perf_diff.json")
    artifacts = [bom_json, geom_json, render_manifest]

    # 步骤编号的分母跟着「本次真的会走几步」走：--perf 关闭时仍是 1/5~5/5（存量 CI 的
    # 控制台日志一个字不变），打开时是 1/6~6/6（多出来的第 3 步就是性能取数）。
    n_steps = 6 if perf else 5

    # Step 1: BOM diff（系统 Python，纯文本解析，不需要 FreeCAD）
    run_step(f"Step 1/{n_steps}: BOM diff",
             [SYSTEM_PYTHON, os.path.join(SCRIPT_DIR, "bom_diff.py"),
              stp_old, stp_new, bom_json],
             artifacts=artifacts)

    # ── fail-closed 守卫：两份文件一个产品都没解析出来，必须报错而不是报「无差异」 ──
    # why（真实实测）：SolidWorks 导出的 STEP 写 `PRODUCT ( 'name'`，而旧的提取正则是
    # `PRODUCT\('`（不容空格）。machineagency/jubilee（45.7MB、143 个实体、804 条装配
    # 关系）因此被解析成 **0 个产品** → 0 个候选 → 0 处差异 → **退出码 0**，一份真实存在
    # 改动的装配体被 CI 闸门放行。这与「子步骤失败必须传导到退出码 2」「不做静默失败」
    # 两条铁律直接冲突：字符串解析失败是一种执行失败，不是「没有差异」。
    # 两边都为空才拦——只有一边为空是合法的「整件替换」，那是真差异（退出码 1）。
    with open(bom_json, encoding="utf-8") as fh:
        bom_probe = json.load(fh)
    n_old = bom_probe.get("old_total_products", 0)
    n_new = bom_probe.get("new_total_products", 0)
    if n_old == 0 and n_new == 0:
        raise PipelineError(
            "no PRODUCT records found in either STEP file "
            f"({os.path.basename(stp_old)}: 0, {os.path.basename(stp_new)}: 0). "
            "Either the files are not STEP assemblies, or their PRODUCT syntax is "
            "not recognised. Refusing to report 'no differences': that would make a "
            "real change look like a clean result."
        )

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
    run_step(f"Step 2/{n_steps}: geometric diff", geom_cmd, timeout=1800, artifacts=artifacts)

    # Step 3: 性能快照 + 性能差异（仅 --perf）
    # 【为什么取数在独立子进程】与布尔运算同一个理由：病态 Shape 上的质量属性查询可能卡死，
    # 只有子进程能被父进程 kill（见 perf_probe.py）。差异计算是纯算术，不碰几何，在本进程
    # 算完直接落盘即可——它没有卡死的可能，也没有必要再付一次进程启动成本。
    if perf:
        budget_s = perf_budget if perf_budget is not None else perf_diff.DEFAULT_BUDGET_S
        rel_tol = perf_tol if perf_tol is not None else perf_diff.DEFAULT_REL_TOL
        # 探针自己会在 budget_s 处停下并如实记账，故父进程的死线只需再覆盖两版的导入开销。
        probe_timeout = 2.0 * budget_s + perf_diff.PROBE_TIMEOUT_MARGIN_S
        run_step(f"Step 3/{n_steps}: performance snapshot",
                 [_fc_python(), os.path.join(SCRIPT_DIR, "perf_probe.py"),
                  stp_old, stp_new, perf_snapshot_json, "--budget", str(budget_s)],
                 timeout=probe_timeout,
                 artifacts=artifacts + [perf_snapshot_json])
        try:
            with open(perf_snapshot_json, "r", encoding="utf-8") as f:
                snapshots = json.load(f)
        except (OSError, ValueError) as exc:
            raise PipelineError(f"cannot read performance snapshot: {exc}") from exc
        try:
            perf_block = perf_diff.diff(snapshots.get("old") or {}, snapshots.get("new") or {},
                                        rel_tol=rel_tol, density_kg_per_mm3=density,
                                        budget=budget_s)
        except (KeyError, TypeError, ValueError) as exc:
            # 快照非法（NaN/缺字段）是执行失败，不是「没有性能差异」——必须给退出码 2。
            raise PipelineError(f"performance diff failed: {exc}") from exc
        with open(perf_json, "w", encoding="utf-8") as f:
            json.dump(perf_block, f, ensure_ascii=False, indent=2)
        if perf_block.get("degraded_reason"):
            # 降级必须留痕（AGENTS.md §3.4）：JSON 里有标记，控制台再喊一声，避免读者把
            # 一个没通过自检的分摊当正常结论。
            print("  WARN: the performance block is degraded (%s) — see perf_diff.json"
                  % perf_block["degraded_reason"])
        print("  OK (performance block written)")

    # Step 4: 渲染截图（FreeCAD Python，需要 GUI / 虚拟显示）
    # render_diff 需要新旧两个 STP：旧版用于现算差异几何体做颜色高亮，并渲染旧版同角度并排图。
    render_cmd = [_fc_python(), os.path.join(SCRIPT_DIR, "render_diff.py"),
                  geom_json, stp_old, stp_new, image_dir,
                  "--lang", i18n.get_lang()]
    if label_old:
        render_cmd += ["--label-old", label_old]
    if label_new:
        render_cmd += ["--label-new", label_new]
    effective_render_timeout = (render_timeout if render_timeout is not None
                                else estimate_render_timeout(geom_json))
    run_step(f"Step 4/{n_steps}: render diff images", render_cmd,
             timeout=effective_render_timeout, artifacts=artifacts)

    # Step 5: 汇总 manifest + 报告（纯标准库，直接在本进程调用，不必起子进程）
    # 先写 diff_manifest.json（PPT 的汇总页要读它拿 skipped / unresolved / alignment），再生成 PPT。
    summary = build_summary(bom_json, geom_json, render_manifest,
                            label_old or "", label_new or "",
                            perf_json=perf_json if perf else None)
    if perf and not summary.get("performance_diff"):
        # 产物读不回来时键在、数字为空：对开了 --perf 的人来说，人读产物会与「没开」一模一样，
        # 所以这里必须出声（JSON 里那个空键是给机器看的，不是给读者看的）。
        print("  NOTE: the performance block could not be read back — the report will "
              "omit the performance section (see perf_diff.json)")
    # 渲染用的是哪条死线，必须落进 manifest：否则「这一步超时了」无法复现，
    # 也无法判断该调大估算还是真出了问题。估算/覆盖值都在这里显形。
    summary.setdefault("settings", {})["render_timeout_s"] = effective_render_timeout
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
    if perf:
        # 只在开了 --perf 时才加这两个键：--perf 关闭时 diff_manifest.json 必须**逐字节
        # 与旧版一致**（向外承诺的向后兼容），加一个值为 null 的键也是变化。
        manifest["perf_snapshot"] = perf_snapshot_json
        manifest["perf_diff"] = perf_json
    with open(manifest_path, "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2)

    # 报告必须在 manifest 之后写：报告的唯一输入就是 manifest（避免两套口径）。
    print(f"\n{'=' * 60}")
    print(f"  [Step 5/{n_steps}: report]")
    print(f"{'=' * 60}")
    report_paths = report.generate(manifest_path, lang=i18n.get_lang())
    manifest["report_html"], manifest["report_md"] = report_paths
    # 报告路径也要落盘：调用方（CI / PR 评论）需要知道去哪儿拿 HTML。
    with open(manifest_path, "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2)

    if export_pptx:
        run_step(f"Step 6/{n_steps}: build PPTX",
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
    """独立执行入口（等价于 ``caddiff diff``，但**不含** ``--perf`` 系列选项——性能摘要只在
    CLI 上暴露，本入口走默认的关闭状态）。返回退出码。"""
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
    ap.add_argument("--render-timeout", type=float, default=None,
                    help="Override the render step deadline in seconds "
                         "(default: estimated as 180 + 60 per difference)")
    args = ap.parse_args(argv)

    try:
        manifest = run_diff(
            args.stp_old, args.stp_new, args.output_dir,
            label_old=args.label_old, label_new=args.label_new, lang=args.lang,
            max_faces=args.max_faces, boolean_timeout=args.boolean_timeout,
            skip_parts=args.skip_parts, min_diff_pct=args.min_diff_pct,
            render_timeout=args.render_timeout,
            export_pptx=args.pptx)
    except PipelineError as exc:
        print(f"\nERROR: {exc}", file=sys.stderr)
        return EXIT_ERROR
    return EXIT_DIFF if manifest["summary"]["has_differences"] else EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
