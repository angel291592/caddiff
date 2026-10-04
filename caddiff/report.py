r"""差异报告生成 —— HTML（人读 / 浏览器）与 Markdown（PR 评论）。

## 为什么报告是独立模块而不是塞进 PPT

决策 D-006：默认产物是 **HTML + Markdown + JSON**，PPT 降为 ``--pptx`` 可选导出。
理由：GitHub PR 里能直接贴 Markdown，浏览器里能直接看 HTML；而 PPT 要求读者装
PowerPoint——对开源用户是纯门槛。PPT 保留给「交给工艺/质量部门」那条真实工作流。

## 为什么只用标准库

本模块跑在**系统 Python** 上（不是 FreeCAD 解释器），且刻意不引入模板引擎：
报告是给人看的一次性产物，为它拉一个 Jinja2 依赖不划算，而 ``html.escape`` +
f-string 足够表达这里需要的全部结构。核心包因此保持零第三方依赖。

## 输入契约

唯一输入是 ``diff_manifest.json``（机器契约，见 AGENTS.md §3）。同目录下读
``images/render_manifest.json`` 拿逐处差异的图片文件名与数值。**不要**去读
``geom_diff.json`` 重新推导结论——那会让报告与 manifest 出现两套口径。
"""
import argparse
import html
import json
import os
import sys
import time

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
if SCRIPT_DIR not in sys.path:
    sys.path.insert(0, SCRIPT_DIR)

import console  # noqa: E402
import i18n  # noqa: E402

console.enable_utf8_output()

# 报告里展示的三张图，顺序即阅读顺序：先看在哪（整体），再看旧版、新版特写。
# 与 build_pptx.py 的 PANELS 保持同一顺序——两处不同序会让报告与 PPT 读起来割裂。
PANELS = ("overview_rect", "closeup_old_rect", "closeup_new_rect")


def _esc(value):
    return html.escape("" if value is None else str(value))


def _fmt_num(value, digits=3):
    """数值格式化：None 原样返回，避免把「没有这个量」显示成 0。"""
    if value is None:
        return None
    try:
        return f"{float(value):.{digits}f}"
    except (TypeError, ValueError):
        return str(value)


def _load_json(path, default):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def _images_dir(manifest):
    """图片目录：优先用 manifest 里记的绝对路径，回退到 manifest 同级的 images/。

    why 要有回退：manifest 可能被单独拷出来（例如 CI 把 JSON 传给了别的 job），
    此时 ``images_dir`` 指向的原路径已经不存在，回退到相对位置仍能工作。
    """
    d = manifest.get("images_dir")
    if d and os.path.isdir(d):
        return d
    return os.path.join(os.path.dirname(os.path.abspath(manifest.get("_path", ""))), "images")


def _image_rel(image_dir, filename):
    """报告与图片的相对路径：报告在 <out>/report.html，图片在 <out>/images/。"""
    if not filename:
        return None
    if not os.path.isabs(image_dir):
        return f"images/{filename}"
    return filename  # 调用方给的是绝对路径时不改写，交给渲染层处理


def _change_title(entry, t):
    """一处差异的标题：零件名 + 变更类型（本地化）。"""
    bn = entry.get("base_name") or "?"
    ctype = entry.get("change_type") or "shape_changed"
    return f"{bn} — {t('report.type.' + ctype)}"


def _change_facts(entry, t):
    """把一处差异的关键数值整理成 (标签, 值) 列表。只列出真实存在的量。"""
    facts = []
    ctype = entry.get("change_type") or "shape_changed"
    if ctype == "moved":
        tr = _fmt_num(entry.get("translation_mm"), 2)
        if tr is not None:
            facts.append((t("report.field.translation"), f"{tr} mm"))
        rot = _fmt_num(entry.get("rotation_deg"), 2)
        if rot is not None and float(rot or 0) > 0:
            facts.append((t("report.field.rotation"), f"{rot}°"))
    else:
        for key, label in (("volume_delta", "report.field.volume_delta"),
                           ("volume_delta_pct", "report.field.volume_delta_pct"),
                           ("removed_volume", "report.field.removed"),
                           ("added_volume", "report.field.added")):
            v = _fmt_num(entry.get(key), 4 if key.endswith("volume") else 2)
            if v is None:
                continue
            unit = " %" if key.endswith("_pct") else " mm³"
            facts.append((t(label), f"{v}{unit}"))
    clusters = entry.get("cluster_details") or []
    if clusters:
        facts.append((t("report.field.clusters"), str(len(clusters))))
    size = entry.get("diff_bbox_size_mm")
    if size:
        try:
            dims = " × ".join(f"{float(x):.1f}" for x in size)
            facts.append((t("report.field.diff_extent"), f"{dims} mm"))
        except (TypeError, ValueError):
            pass
    if entry.get("overview_view_fallback"):
        facts.append((t("report.field.view"), t("report.view.internal")))
    return facts


def _honesty_items(summary, t):
    """「诚实性」段落：没比上的、被跳过的、被筛掉的，必须让读者看见。"""
    items = []
    for sp in summary.get("skipped_parts") or []:
        name = sp.get("name") or "?"
        reason = sp.get("reason") or "unknown"
        faces = sp.get("faces")
        extra = f" ({faces} faces)" if faces else ""
        items.append(f"**{_esc(name)}** — {t('report.skipped.reason.' + _reason_key(reason))}{extra}")
    for ft in summary.get("filtered_by_threshold") or []:
        items.append(f"**{_esc(ft.get('name'))}** — "
                     f"{t('report.filtered', pct=_fmt_num(ft.get('volume_delta_pct'), 2),
                          threshold=_fmt_num(ft.get('threshold_pct'), 2))}")
    for note in summary.get("unresolved_notes") or []:
        items.append(f"**{_esc(note.get('base_name'))}** — {_esc(note.get('note'))}")
    return items


def _reason_key(reason):
    """把机器枚举映射到文案键。未知原因归到 other，不猜语义。"""
    known = {"user_skipped", "no_shape", "too_complex", "timeout", "assembly_container"}
    if reason in known:
        return reason
    if reason.startswith("brep_export_failed"):
        return "brep_export_failed"
    if reason.startswith("worker_crashed"):
        return "worker_crashed"
    if reason.startswith("worker_failed"):
        return "worker_failed"
    return "other"


# ────────────────────────────── Markdown ──────────────────────────────

def render_markdown(manifest, entries, t):
    s = manifest.get("summary") or {}
    out = []
    out.append(f"# {t('report.title', old=manifest.get('label_old') or '?', new=manifest.get('label_new') or '?')}")
    out.append("")
    if not s.get("has_differences"):
        out.append(f"**{t('report.none')}**")
        out.append("")
    else:
        out.append(t("report.summary_line",
                     geometry=s.get("geometry_diff_count", 0),
                     bom=s.get("bom_diff_count", 0)))
        out.append("")

    if s.get("global_alignment_warning"):
        ga = s.get("global_alignment") or {}
        out.append(f"> ⚠️ **{t('report.warn.alignment')}** "
                   f"(median shift {_fmt_num(ga.get('median_shift_mm'), 2)} mm, "
                   f"consistency {_fmt_num(ga.get('direction_consistency'), 2)})")
        out.append("")

    bom = _bom_section(s, t)
    if bom:
        out.extend(bom)
        out.append("")

    if entries:
        out.append(f"## {t('report.section.changes')}")
        out.append("")
        for i, entry in enumerate(entries, 1):
            out.append(f"### {i}. {_change_title(entry, t)}")
            out.append("")
            facts = _change_facts(entry, t)
            if facts:
                for label, value in facts:
                    out.append(f"- {label}: `{value}`")
                out.append("")
            for panel in PANELS:
                fn = entry.get(panel)
                if fn:
                    out.append(f"![{_change_title(entry, t)} — {t('report.panel.' + panel)}](images/{fn})")
                    out.append("")

    honesty = _honesty_items(s, t)
    if honesty:
        out.append(f"## {t('report.section.skipped')}")
        out.append("")
        for item in honesty:
            out.append(f"- {item}")
        out.append("")

    out.append("---")
    out.append(t("report.footer", version=manifest.get("manifest_version", "?")))
    out.append("")
    return "\n".join(out)


def _bom_section(summary, t):
    added = summary.get("bom_added") or []
    removed = summary.get("bom_removed") or []
    mismatch = summary.get("bom_count_mismatch") or []
    if not (added or removed or mismatch):
        return []
    out = [f"## {t('report.section.bom')}", ""]
    for label, items in (("report.bom.added", added),
                         ("report.bom.removed", removed),
                         ("report.bom.count_mismatch", mismatch)):
        if items:
            joined = ", ".join(f"`{_esc(x)}`" for x in items)
            out.append(f"- **{t(label)}** ({len(items)}): {joined}")
    out.append("")
    return out


# ────────────────────────────── HTML ──────────────────────────────

_CSS = """
:root { color-scheme: light dark; --fg:#1b1b1f; --muted:#6b6b76; --line:#e3e3e8;
        --bg:#ffffff; --card:#fafafc; --accent:#b3006b; }
@media (prefers-color-scheme: dark) {
  :root { --fg:#e8e8ee; --muted:#a0a0ad; --line:#33333c; --bg:#16161a; --card:#1e1e24;
          --accent:#ff5fb0; }
}
* { box-sizing: border-box; }
body { margin:0; padding:2rem 1.25rem 4rem; background:var(--bg); color:var(--fg);
       font:15px/1.6 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,"Helvetica Neue",Arial,sans-serif; }
main { max-width: 1080px; margin: 0 auto; }
h1 { font-size:1.6rem; margin:0 0 .25rem; }
h2 { font-size:1.15rem; margin:2.5rem 0 .75rem; padding-bottom:.35rem; border-bottom:1px solid var(--line); }
h3 { font-size:1rem; margin:1.75rem 0 .5rem; }
.sub { color:var(--muted); margin:0 0 1.5rem; }
.verdict { display:inline-block; padding:.3rem .7rem; border-radius:999px; font-weight:600;
           background:var(--card); border:1px solid var(--line); }
.verdict.diff { color:var(--accent); border-color:var(--accent); }
.warn { background:var(--card); border-left:3px solid #d9822b; padding:.7rem .9rem;
        border-radius:4px; margin:1rem 0; }
table { border-collapse:collapse; width:100%; margin:.5rem 0 1rem; font-size:.94rem; }
th,td { text-align:left; padding:.4rem .6rem; border-bottom:1px solid var(--line); vertical-align:top; }
th { color:var(--muted); font-weight:600; }
code { background:var(--card); padding:.1rem .35rem; border-radius:4px; font-size:.9em; }
.shots { display:grid; grid-template-columns:repeat(auto-fit,minmax(260px,1fr)); gap:.75rem; margin:.75rem 0 0; }
.shots figure { margin:0; background:var(--card); border:1px solid var(--line); border-radius:8px; overflow:hidden; }
.shots img { width:100%; display:block; }
.shots figcaption { padding:.4rem .6rem; color:var(--muted); font-size:.85rem; }
ul { padding-left:1.2rem; }
footer { margin-top:3rem; color:var(--muted); font-size:.85rem; border-top:1px solid var(--line); padding-top:1rem; }
"""


def render_html(manifest, entries, t):
    s = manifest.get("summary") or {}
    has = bool(s.get("has_differences"))
    old = _esc(manifest.get("label_old") or "?")
    new = _esc(manifest.get("label_new") or "?")
    p = []
    p.append("<!doctype html>")
    p.append('<html lang="%s">' % _esc(i18n.get_lang()))
    p.append("<head>")
    p.append('<meta charset="utf-8">')
    p.append('<meta name="viewport" content="width=device-width,initial-scale=1">')
    p.append(f"<title>{_esc(t('report.title', old=old, new=new))}</title>")
    p.append(f"<style>{_CSS}</style>")
    p.append("</head><body><main>")

    p.append(f"<h1>{_esc(t('report.title', old=old, new=new))}</h1>")
    p.append(f'<p class="sub">{_esc(os.path.basename(manifest.get("stp_old") or ""))} → '
             f'{_esc(os.path.basename(manifest.get("stp_new") or ""))}</p>')

    if has:
        p.append(f'<p><span class="verdict diff">{_esc(t("report.summary_line", geometry=s.get("geometry_diff_count", 0), bom=s.get("bom_diff_count", 0)))}</span></p>')
    else:
        p.append(f'<p><span class="verdict">{_esc(t("report.none"))}</span></p>')

    if s.get("global_alignment_warning"):
        ga = s.get("global_alignment") or {}
        p.append(f'<div class="warn"><strong>{_esc(t("report.warn.alignment"))}</strong><br>'
                 f'median shift {_fmt_num(ga.get("median_shift_mm"), 2)} mm · '
                 f'direction consistency {_fmt_num(ga.get("direction_consistency"), 2)} · '
                 f'paired parts {_esc(ga.get("paired_count"))}</div>')

    bom_rows = _bom_rows(s, t)
    if bom_rows:
        p.append(f"<h2>{_esc(t('report.section.bom'))}</h2>")
        p.append("<table><tr><th>Kind</th><th>Parts</th></tr>")
        for label, items in bom_rows:
            p.append(f"<tr><td>{_esc(label)}</td><td>"
                     + ", ".join(f"<code>{_esc(x)}</code>" for x in items) + "</td></tr>")
        p.append("</table>")

    if entries:
        p.append(f"<h2>{_esc(t('report.section.changes'))}</h2>")
        for i, entry in enumerate(entries, 1):
            p.append(f"<h3>{i}. {_esc(_change_title(entry, t))}</h3>")
            facts = _change_facts(entry, t)
            if facts:
                p.append("<table>")
                for label, value in facts:
                    p.append(f"<tr><th>{_esc(label)}</th><td><code>{_esc(value)}</code></td></tr>")
                p.append("</table>")
            shots = [(panel, entry.get(panel)) for panel in PANELS if entry.get(panel)]
            if shots:
                p.append('<div class="shots">')
                for panel, fn in shots:
                    p.append('<figure><img loading="lazy" src="images/%s" alt="%s">'
                             '<figcaption>%s</figcaption></figure>'
                             % (_esc(fn), _esc(_change_title(entry, t)),
                                _esc(t("report.panel." + panel))))
                p.append("</div>")

    honesty = _honesty_items(s, t)
    if honesty:
        p.append(f"<h2>{_esc(t('report.section.skipped'))}</h2><ul>")
        for item in honesty:
            p.append(f"<li>{item}</li>")  # 已在上游 escape，含 ** 强调标记，故用 md-lite 转换
        p.append("</ul>")

    settings = s.get("settings") or {}
    if settings:
        p.append(f"<h2>{_esc(t('report.section.settings'))}</h2><table>")
        for k in sorted(settings):
            p.append(f"<tr><th>{_esc(k)}</th><td><code>{_esc(settings[k])}</code></td></tr>")
        p.append("</table>")

    p.append(f"<footer>{_esc(t('report.footer', version=manifest.get('manifest_version', '?')))}"
             f"<br>{_esc(time.strftime('%Y-%m-%d %H:%M:%S'))}</footer>")
    p.append("</main></body></html>")
    return "\n".join(p)


def _bom_rows(summary, t):
    rows = []
    for label, key in (("report.bom.added", "bom_added"),
                       ("report.bom.removed", "bom_removed"),
                       ("report.bom.count_mismatch", "bom_count_mismatch")):
        items = summary.get(key) or []
        if items:
            rows.append((t(label), items))
    return rows


def generate(manifest_path, lang=None):
    """读 manifest，写出 ``report.html`` 与 ``report.md``。返回两者的绝对路径。"""
    if lang:
        i18n.set_lang(lang)
    t = i18n.t

    manifest_path = os.path.abspath(manifest_path)
    manifest = _load_json(manifest_path, None)
    if manifest is None:
        raise RuntimeError(f"cannot read manifest: {manifest_path}")
    manifest["_path"] = manifest_path

    image_dir = _images_dir(manifest)
    render_manifest = manifest.get("render_manifest")
    entries = _load_json(render_manifest, []) if render_manifest else []
    if not isinstance(entries, list):
        entries = []

    out_dir = os.path.dirname(manifest_path)
    html_path = os.path.join(out_dir, "report.html")
    md_path = os.path.join(out_dir, "report.md")

    with open(html_path, "w", encoding="utf-8") as f:
        f.write(render_html(manifest, entries, t))
    with open(md_path, "w", encoding="utf-8") as f:
        f.write(render_markdown(manifest, entries, t))

    print(f"Report written: {html_path}")
    print(f"Report written: {md_path}")
    return html_path, md_path


def main(argv=None):
    ap = argparse.ArgumentParser(description="Generate HTML/Markdown reports from a diff manifest")
    ap.add_argument("manifest", help="path to diff_manifest.json")
    ap.add_argument("--lang", default=None, choices=i18n.available_langs(),
                    help="language of the generated report (default: $CADDIFF_LANG, else en)")
    args = ap.parse_args(argv)
    try:
        generate(args.manifest, lang=args.lang)
    except RuntimeError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
