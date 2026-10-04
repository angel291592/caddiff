"""
PPT拼版：根据render_manifest.json生成差异对比PPT。
第1页 = 汇总页（差异清单表 + 警告区），后续每页 = 标题条 + 三图横排 + 信息带。

文字带是刚需而非装饰：差异体积只占零件体量的1%左右，图能让人看到"哪里改了"，
但"改了多少、改的是什么"必须落成文字，否则汇报时仍要读者自己看图猜。

【版式设计要点（用户反馈驱动，改版前先读）】
1. 信息带不能是几行等粗的纯文本——用户反馈"排版不好看"。现在改为左右两栏 + 项目符号 +
   标签/数值分色：左栏放可量化的数值（尺寸、增减料），右栏放定性描述（形状、视角），
   底部单独一行小字图例。关键数值用色彩强调（增料绿、减料橙、品红说明用品红）。
2. 一切字号用 Pt() 显式给定，不依赖模板继承——空白版式（layout[6]）的默认字号是18pt，
   继承会让信息带过大挤出页面。
用系统Python运行：
    python build_pptx.py <render_manifest.json> <output_pptx>
"""
import argparse
import json
import sys
import os
from pptx import Presentation
from pptx.dml.color import RGBColor
from pptx.util import Pt
from pptx.enum.text import PP_ALIGN, MSO_ANCHOR

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
if SCRIPT_DIR not in sys.path:
    sys.path.insert(0, SCRIPT_DIR)

import console  # noqa: E402
import i18n  # noqa: E402  （产出物文案唯一真相源，见 AGENTS.md §4）

console.enable_utf8_output()

SLIDE_WIDTH_EMU = 12192000
SLIDE_HEIGHT_EMU = 6858000
MARGIN_EMU = 420000
TITLE_BAR_H = 560000        # 顶部标题条
# 图片自带的方向标记带里已写明"旧版 V4 特写"这类标题，PPT 再放一行图题就是重复，
# 实测导出图里两处文字上下紧贴、显得啰嗦。故取消独立图题行，把高度让给图区与信息带。
CAPTION_H = 0
# 信息带高度。必须按【行数最多的那一页】留够，不是按当前页（坑 G4）。
# 演进：1.28M 时第1页图例行被挤出页面下沿 → 1.62M 三页都放得下（当时左栏恒为 3 行）；
# 加入逐簇尺寸后，多差异点的页面左栏变成 1(共N处)+N(逐簇明细)+2(增减料) 行，
# N=2 时共 5 行、比原来多 2 行，实测 1.62M 下"新增材料"那行直接压在图例上（导出图确认）。
# 12.5pt + line_spacing 1.15 + space_after 5pt ≈ 246062 EMU/行，5 行需 1.23M，
# 加 130000 顶距 + LEGEND_H + 余量 → 取 1.78M。
# 上限校验：图片在版式里受【列宽】限制而非高度限制（aspect 1.10 > col_w/img_area_h），
# 只要 BAND_H < 约 1.97M 图片显示尺寸就不受影响，故加高不损失图区。
BAND_H = 1780000
# 图例行高度。曾给 250000 EMU，实测导出图里被裁掉下半截（PowerPoint 行盒比字号本身高）。
LEGEND_H = 300000
GAP_EMU = 130000

# 配色：与渲染图里的品红高亮呼应，整体走冷灰 + 单点强调，避免PPT花哨
C_TITLE = RGBColor(0x1A, 0x1A, 0x22)
C_LABEL = RGBColor(0x6B, 0x6B, 0x78)      # 字段名
C_VALUE = RGBColor(0x1A, 0x1A, 0x22)      # 字段值
C_ACCENT = RGBColor(0xC0, 0x00, 0x60)     # 品红系强调
C_ADD = RGBColor(0x1B, 0x7F, 0x3B)        # 增料 绿
C_REMOVE = RGBColor(0xC0, 0x5A, 0x00)     # 减料 橙
C_MUTED = RGBColor(0x8A, 0x8A, 0x96)      # 图例灰
C_RULE = RGBColor(0xD8, 0xD8, 0xE0)       # 分隔线
C_BAND_BG = RGBColor(0xF7, 0xF7, 0xFA)    # 信息带底色

# 三图横排的 manifest 键，顺序即从左到右的摆放顺序。
# 不再配文字图题：每张图自带的方向标记带里已写明"旧版 V4 特写"这类标题（render_diff.py 画的），
# PPT 再放一行就是重复。标记带同时带有视线方向向量，比静态图题信息量更大。
PANELS = ["closeup_old_rect", "closeup_new_rect", "overview_rect"]

# 差异簇编号的圈号字符，必须与 render_diff.draw_rects_on_image 画在框上的一致，
# 否则读者无法把左栏的尺寸清单对应到图上的框。
CIRCLED = "①②③④⑤⑥⑦⑧"

# 汇总页常量
SUMMARY_ROWS_PER_PAGE = 22       # 每页汇总表行数上限
DETAIL_PAGE_LIMIT = 20           # 详情页上限（按重要度取前 N 处）
SUMMARY_HEADER_H = 560000        # 汇总页标题区高度
SUMMARY_TABLE_TOP = 800000       # 表格起始位置（EMU）
SUMMARY_ROW_H = 220000           # 每行高度（EMU）
SUMMARY_COL_W = [520000, 1780000, 1480000, 1300000, 960000, 1880000, 700000]
# 序号 / 零件名 / 类型 / 体积变化 / 占比 / 差异尺寸 / 详情页
SUMMARY_FONT = 9.5               # 表内字号
SUMMARY_HEADER_FONT = 10         # 表头字号


def summary_col_labels():
    """汇总表表头文案。

    why 是函数而不是模块级常量：文案要走 i18n，而 import 时 `--lang` 还没解析
    （AGENTS.md §4：模块级常量里不许放文案）。列数与 SUMMARY_COL_W 必须一致。
    """
    return [i18n.t("pptx.col.index"), i18n.t("pptx.col.part_name"),
            i18n.t("pptx.col.type"), i18n.t("pptx.col.volume_delta"),
            i18n.t("pptx.col.share"), i18n.t("pptx.col.diff_size"),
            i18n.t("pptx.col.detail_page")]


def get_image_size(img_path):
    from PIL import Image
    img = Image.open(img_path)
    return img.width, img.height


def fmt(v, digits=2):
    """数值格式化，None安全。"""
    if v is None:
        return "—"
    return f"{v:.{digits}f}"


def add_rect(slide, left, top, width, height, fill=None, line=None):
    """画一个无文字的矩形（用作底色块/分隔线）。"""
    from pptx.enum.shapes import MSO_SHAPE
    shp = slide.shapes.add_shape(MSO_SHAPE.RECTANGLE, left, top, width, height)
    if fill is None:
        shp.fill.background()
    else:
        shp.fill.solid()
        shp.fill.fore_color.rgb = fill
    if line is None:
        shp.line.fill.background()
    else:
        shp.line.color.rgb = line
        shp.line.width = Pt(0.75)
    shp.shadow.inherit = False
    return shp


def new_textbox(slide, left, top, width, height, anchor=MSO_ANCHOR.TOP):
    box = slide.shapes.add_textbox(left, top, width, height)
    tf = box.text_frame
    tf.word_wrap = True
    tf.vertical_anchor = anchor
    tf.margin_left = 0
    tf.margin_right = 0
    tf.margin_top = 0
    tf.margin_bottom = 0
    return tf


def put_runs(paragraph, runs, size, space_after_pt=0, line_spacing=None,
             underline=False):
    """把 [(文本, 颜色, 是否加粗), ...] 写成同一段里的多个 run。
    分色分粗细必须用 run 级别设置——段落级只能整段统一，做不出"标签灰 + 数值黑加粗"。"""
    paragraph.space_after = Pt(space_after_pt)
    if line_spacing:
        paragraph.line_spacing = line_spacing
    for text, color, bold in runs:
        r = paragraph.add_run()
        r.text = text
        r.font.size = Pt(size)
        r.font.bold = bold
        r.font.color.rgb = color
        if underline:
            r.font.underline = True
    return paragraph


def add_field_lines(tf, fields, size, first=True):
    """输出若干"▪ 标签  值"行，标签灰色、值黑色加粗。

    fields 每项可以是 (label, value, vcolor) 或 (label, value, vcolor, indent)；
    indent=True 的行用缩进小圆点代替品红方块，表示它是上一行的下级明细（如逐簇尺寸）。
    """
    for i, item in enumerate(fields):
        label, value, vcolor = item[0], item[1], item[2]
        indent = item[3] if len(item) > 3 else False
        p = tf.paragraphs[0] if (first and i == 0) else tf.add_paragraph()
        bullet = ("        ", C_MUTED, False) if indent else ("▪  ", C_ACCENT, True)
        put_runs(p, [
            bullet,
            (f"{label}", C_LABEL, False),
            ("    ", C_LABEL, False),
            (value, vcolor, True),
        ], size, space_after_pt=5, line_spacing=1.15)


def cluster_size_lines(entry):
    """返回要写进左栏的行列表。多处差异时每簇独占一行，编号与图上红框标注对应。

    why 不能只报并集尺寸：对称差常由多块空间分离的材料构成，并集 bbox 会把整个零件囊括进来。
    实测 PART-A 的两块增料中心相距 18.3mm，并集尺寸 19.17×10.97×3.21mm 约等于整个零件，
    而真实的两块是 1.29×10.97×1.23 与 0.51×3.01×0.56 —— 报并集会误导读者。

    why 多簇必须每簇独占一行（导出图目视发现，坑 G1）：把两簇挤在同一行时，
    左栏只有 33% 版面宽度，实测换行点落在"② 0.51×..."中间，圈号被孤零零留在上一行行尾，
    读者无法把它和尺寸对应起来。
    返回 [(标签, 值, 是否缩进续行), ...]。
    """
    details = entry.get("cluster_details") or []
    if not details:
        # 向后兼容：旧版 manifest 没有分簇字段时退回并集尺寸
        size_mm = entry.get("diff_bbox_size_mm") or []
        val = (" × ".join(fmt(v) for v in size_mm) + " mm") if size_mm else "—"
        return [(i18n.t("pptx.field.diff_region_size"), val, False)]
    if len(details) == 1:
        s = details[0].get("size_mm") or []
        val = (" × ".join(fmt(v) for v in s) + " mm") if s else "—"
        return [(i18n.t("pptx.field.diff_region_size"), val, False)]
    lines = [(i18n.t("pptx.field.diff_region_size"),
              i18n.t("pptx.value.cluster_count", n=len(details)), False)]
    for d in details:
        s = d.get("size_mm") or []
        idx = d.get("index", 0)
        mark = CIRCLED[idx - 1] if 1 <= idx <= len(CIRCLED) else str(idx)
        role = (i18n.t("pptx.role.added") if d.get("role") == "added"
                else i18n.t("pptx.role.removed"))
        lines.append((f"{mark} {role}",
                      " × ".join(fmt(v) for v in s) + " mm", True))
    return lines


def build_empty_slide(prs, summary):
    """无差异时的单页 PPT。

    why 不 exit 非零："两版几何相同"是有效结论（只改了标注或元数据是真实场景），
    不是失败。早先上游不写 manifest、这里直接 FileNotFoundError，
    用户无法区分"没有差异"与"程序坏了"。

    why 要列出被跳过的零件：没有这一段，被跳过的零件在 PPT 里完全不可见，
    读者会把"没提到"理解成"没差异"。
    """
    slide = prs.slides.add_slide(prs.slide_layouts[6])
    content_w = SLIDE_WIDTH_EMU - 2 * MARGIN_EMU
    bar_w = 46000
    add_rect(slide, MARGIN_EMU, MARGIN_EMU, bar_w, TITLE_BAR_H - 180000, fill=C_ACCENT)
    tf = new_textbox(slide, MARGIN_EMU + bar_w + 130000, MARGIN_EMU,
                     content_w * 0.8, TITLE_BAR_H - 180000)
    put_runs(tf.paragraphs[0], [(i18n.t("pptx.empty.title"), C_TITLE, True)], 22,
             space_after_pt=3, underline=True)
    put_runs(tf.add_paragraph(), [
        (f"{summary.get('label_old') or i18n.t('label.old')}  →  "
         f"{summary.get('label_new') or i18n.t('label.new')}", C_MUTED, False),
    ], 11.5, space_after_pt=0)

    body_top = MARGIN_EMU + TITLE_BAR_H + GAP_EMU
    tf = new_textbox(slide, MARGIN_EMU, body_top, content_w,
                     SLIDE_HEIGHT_EMU - body_top - MARGIN_EMU)
    fields = [
        (i18n.t("pptx.field.candidates"), str(summary.get("total_candidates") or "—"), C_VALUE),
        (i18n.t("pptx.field.conclusion"),
         summary.get("reason") or i18n.t("summary.no_geometry_diff"), C_VALUE),
    ]
    ga = summary.get("global_alignment") or {}
    if ga.get("misaligned"):
        fields.append((i18n.t("pptx.field.alignment_warning"),
                       i18n.t("pptx.alignment.warning",
                              median=ga.get("median_shift_mm"),
                              consistency=ga.get("direction_consistency")),
                       C_REMOVE))
    add_field_lines(tf, fields, 13)

    # 诚实性区块：被跳过 / 未解析 / 数量不一致的零件必须显式列出
    skipped = summary.get("skipped_parts") or []
    notes = summary.get("notes") or []
    filtered = summary.get("filtered_by_threshold") or []
    if skipped or notes or filtered:
        p = tf.add_paragraph()
        put_runs(p, [("▪  ", C_ACCENT, True),
                     (i18n.t("pptx.empty.skipped_header"), C_LABEL, True)],
                 13, space_after_pt=4, line_spacing=1.15)
        for s in skipped:
            put_runs(tf.add_paragraph(), [
                ("        ", C_MUTED, False),
                (f"{s.get('name')}", C_VALUE, True),
                (f"    {i18n.t('pptx.skipped.reason', reason=s.get('reason'))}"
                 + (i18n.t("pptx.skipped.faces", n=s.get("faces")) if s.get("faces") else ""),
                 C_MUTED, False),
            ], 11.5, space_after_pt=3, line_spacing=1.15)
        for n in notes:
            put_runs(tf.add_paragraph(), [
                ("        ", C_MUTED, False),
                (f"{n.get('base_name')}", C_VALUE, True),
                (f"    {n.get('note')}", C_MUTED, False),
            ], 11.5, space_after_pt=3, line_spacing=1.15)
        for f_ in filtered:
            put_runs(tf.add_paragraph(), [
                ("        ", C_MUTED, False),
                (f"{f_.get('name')}", C_VALUE, True),
                ("    " + i18n.t("pptx.filtered.below_threshold",
                                 delta=fmt(f_.get("volume_delta")),
                                 pct=fmt(f_.get("volume_delta_pct")),
                                 threshold=f_.get("threshold_pct")), C_MUTED, False),
            ], 11.5, space_after_pt=3, line_spacing=1.15)



def _load_summary(manifest_path):
    """从 diff_manifest.json 读取 summary 块（含 skipped/unresolved/alignment 等）。

    manifest_path 是 render_manifest.json 的路径，diff_manifest.json 在父目录。
    """
    parent = os.path.dirname(os.path.dirname(manifest_path))
    dm_path = os.path.join(parent, "diff_manifest.json")
    if os.path.exists(dm_path):
        with open(dm_path, "r", encoding="utf-8") as f:
            dm = json.load(f)
        return dm.get("summary") or {}
    return {}


def build_summary_page(prs, manifest, summary):
    """生成汇总页（第 1 页起，行数过多时自动续页）。

    内容：差异清单表（按体积变化占比降序）+ 警告区（skipped/unresolved/alignment）。
    排在详情页之前，表内每行标注"详情页"页码（从汇总页最后一页之后开始算）。
    """
    # 按体积变化占比降序排列（重要度定义，写进代码注释）
    sorted_manifest = sorted(manifest, key=lambda e: abs(e.get("volume_delta_pct") or 0), reverse=True)
    total = len(sorted_manifest)
    # 详情页上限截断
    limited = total > DETAIL_PAGE_LIMIT
    if limited:
        detail_list = sorted_manifest[:DETAIL_PAGE_LIMIT]
    else:
        detail_list = sorted_manifest

    # 汇总表行数据
    rows = []
    for i, entry in enumerate(sorted_manifest):
        ctype = (i18n.t("pptx.change_type.moved") if entry.get("change_type") == "moved"
                 else i18n.t("pptx.change_type.shape"))
        vd = entry.get("volume_delta")
        vp = entry.get("volume_delta_pct")
        vol_str = fmt(vd) if vd is not None else "—"
        pct_str = f"{fmt(vp)}%" if vp is not None else "—"
        # 差异区域尺寸：取 cluster_details 各簇的尺寸拼接
        clusters = entry.get("cluster_details") or []
        if clusters:
            sizes = [" × ".join(fmt(v) for v in (c.get("size_mm") or [])) for c in clusters]
            size_str = "  |  ".join(sizes)
        else:
            bbox = entry.get("diff_bbox_size_mm") or []
            size_str = " × ".join(fmt(v) for v in bbox) + " mm" if bbox else "—"
        # 详情页页码：只有进入 detail_list 的才有页码
        if entry in detail_list:
            page_num = detail_list.index(entry) + 1  # 1-based within detail pages
        else:
            page_num = "—"
        rows.append({
            "entry": entry, "idx": i + 1, "ctype": ctype,
            "vol": vol_str, "pct": pct_str, "size": size_str, "page": page_num,
        })

    # 汇总页数：表头 + 数据行，每页 SUMMARY_ROWS_PER_PAGE 行
    total_rows = len(rows)
    summary_pages = (total_rows + SUMMARY_ROWS_PER_PAGE - 1) // SUMMARY_ROWS_PER_PAGE
    if summary_pages < 1:
        summary_pages = 1

    detail_page_offset = summary_pages  # 详情页从汇总页之后开始编号

    content_w = SLIDE_WIDTH_EMU - 2 * MARGIN_EMU
    label_old = summary.get("label_old") or i18n.t("label.old")
    label_new = summary.get("label_new") or i18n.t("label.new")

    for sp in range(summary_pages):
        slide = prs.slides.add_slide(prs.slide_layouts[6])
        start_row = sp * SUMMARY_ROWS_PER_PAGE
        end_row = min(start_row + SUMMARY_ROWS_PER_PAGE, total_rows)
        page_rows = rows[start_row:end_row]

        # ===== 标题区 =====
        bar_w = 46000
        add_rect(slide, MARGIN_EMU, MARGIN_EMU, bar_w, SUMMARY_HEADER_H - 180000, fill=C_ACCENT)
        tf = new_textbox(slide, MARGIN_EMU + bar_w + 130000, MARGIN_EMU,
                         content_w * 0.8, SUMMARY_HEADER_H - 180000)
        title = i18n.t("pptx.summary.title", old=label_old, new=label_new)
        if summary_pages > 1:
            title += f"  ({sp + 1}/{summary_pages})"
        put_runs(tf.paragraphs[0], [(title, C_TITLE, True)], 22, space_after_pt=3, underline=True)
        put_runs(tf.add_paragraph(), [
            (i18n.t("pptx.summary.stats", count=total,
                    candidates=summary.get("total_candidates") or "—"), C_MUTED, False),
        ], 11.5, space_after_pt=0)
        if limited:
            put_runs(tf.add_paragraph(), [
                (i18n.t("pptx.summary.limited", limit=DETAIL_PAGE_LIMIT), C_ACCENT, True),
            ], 11, space_after_pt=0)

        # ===== 差异汇总表 =====
        table_top = SUMMARY_TABLE_TOP + (sp == 0) * 0  # 只有第一页需要标题区
        n_rows = len(page_rows) + 1  # +1 for header
        n_cols = len(summary_col_labels())
        total_table_w = sum(SUMMARY_COL_W)
        table_left = (SLIDE_WIDTH_EMU - total_table_w) // 2

        tbl_shape = slide.shapes.add_table(n_rows, n_cols, table_left, table_top,
                                           total_table_w, SUMMARY_ROW_H * n_rows)
        tbl = tbl_shape.table

        # 列宽
        for ci, w in enumerate(SUMMARY_COL_W):
            tbl.columns[ci].width = w

        # 表头
        for ci, label in enumerate(summary_col_labels()):
            cell = tbl.cell(0, ci)
            cell.text = ""
            p = cell.text_frame.paragraphs[0]
            p.alignment = PP_ALIGN.CENTER
            r = p.add_run()
            r.text = label
            r.font.size = Pt(SUMMARY_HEADER_FONT)
            r.font.bold = True
            r.font.color.rgb = C_LABEL
            # 表头底色
            cell.fill.solid()
            cell.fill.fore_color.rgb = C_BAND_BG

        # 数据行
        for ri, row_data in enumerate(page_rows):
            base_name = row_data["entry"].get("base_name", "?")
            # 零件名截断（防止超长名称撑破表格）
            if len(base_name) > 28:
                base_name = base_name[:26] + "…"
            vals = [
                str(row_data["idx"]),
                base_name,
                row_data["ctype"],
                row_data["vol"],
                row_data["pct"],
                row_data["size"],
                str(row_data["page"]),
            ]
            for ci, val in enumerate(vals):
                cell = tbl.cell(ri + 1, ci)
                cell.text = ""
                p = cell.text_frame.paragraphs[0]
                p.alignment = PP_ALIGN.CENTER if ci != 1 else PP_ALIGN.LEFT
                r = p.add_run()
                r.text = val
                r.font.size = Pt(SUMMARY_FONT)
                r.font.color.rgb = C_VALUE
                if ci == 0:
                    r.font.bold = True
                elif ci == 3 or ci == 4:
                    r.font.color.rgb = C_ACCENT if row_data["pct"] != "—" else C_MUTED
            # 交替行底色
            if ri % 2 == 1:
                for ci in range(n_cols):
                    tbl.cell(ri + 1, ci).fill.solid()
                    tbl.cell(ri + 1, ci).fill.fore_color.rgb = C_BAND_BG

        # ===== 警告区（只在最后一页汇总页显示）=====
        if sp == summary_pages - 1:
            skipped = summary.get("skipped_parts") or []
            notes = summary.get("unresolved_notes") or []
            filtered = summary.get("filtered_by_threshold") or []
            ga = summary.get("global_alignment") or {}
            has_warn = skipped or notes or filtered or ga.get("misaligned")
            if has_warn:
                # 预计算警告区总高度，判断当前页剩余空间是否足够；
                # 不够则新开一页，避免警告内容溢出或截断。
                warn_items = []
                warn_heights = []
                if ga.get("misaligned"):
                    warn_items.append("alignment")
                    warn_heights.append(280000)
                for s in skipped:
                    warn_items.append(
                        i18n.t("pptx.warn.item", name=s.get("name"), reason=s.get("reason"))
                        + (i18n.t("pptx.skipped.faces", n=s.get("faces")) if s.get("faces") else ""))
                    warn_heights.append(180000)
                for n in notes:
                    warn_items.append(i18n.t("pptx.warn.item", name=n.get("base_name"),
                                             reason=n.get("note")))
                    warn_heights.append(180000)
                for f_ in filtered:
                    warn_items.append(i18n.t("pptx.warn.filtered",
                                             name=f_.get("name"),
                                             delta=fmt(f_.get("volume_delta")),
                                             pct=fmt(f_.get("volume_delta_pct"))))
                    warn_heights.append(180000)
                total_warn_h = 250000 + sum(warn_heights)  # 标题 + 内容
                # 表底：表格创建时指定的高度，加 4 行余量防止 python-pptx 实际渲染比指定高度更高
                table_bottom = table_top + SUMMARY_ROW_H * (n_rows + 4)
                warn_top = table_bottom + 220000
                page_bottom = SLIDE_HEIGHT_EMU - MARGIN_EMU
                if warn_top + total_warn_h > page_bottom:
                    # 当前页放不下，新开一页放警告区（无需分隔线，新页没有表格）
                    slide = prs.slides.add_slide(prs.slide_layouts[6])
                    warn_top = SUMMARY_TABLE_TOP
                    summary_pages += 1
                else:
                    # 分隔线（表底 + 100000，确保在表格下方）
                    add_rect(slide, table_left, table_bottom + 100000,
                             total_table_w, 18000, fill=C_RULE)
                # 警告区标题
                tf = new_textbox(slide, table_left, warn_top, total_table_w, 250000)
                put_runs(tf.paragraphs[0], [
                    ("⚠  ", C_REMOVE, True),
                    (i18n.t("pptx.warn.header"), C_LABEL, True),
                ], 11, space_after_pt=4, line_spacing=1.15)
                warn_y = warn_top + 250000

                # 配准警告
                if ga.get("misaligned"):
                    tf2 = new_textbox(slide, table_left, warn_y, total_table_w, 280000)
                    put_runs(tf2.paragraphs[0], [
                        ("▪  ", C_ACCENT, True),
                        (i18n.t("pptx.warn.alignment_label"), C_LABEL, True),
                        (i18n.t("pptx.alignment.warning",
                                median=ga.get("median_shift_mm"),
                                consistency=ga.get("direction_consistency")),
                         C_MUTED, False),
                    ], 10, space_after_pt=3, line_spacing=1.15)
                    warn_y += 280000

                # 被跳过/未解析/被筛掉的零件
                for item in warn_items:
                    if item == "alignment":
                        continue  # 已在上面处理
                    tf3 = new_textbox(slide, table_left + 120000, warn_y,
                                      total_table_w - 120000, 200000)
                    put_runs(tf3.paragraphs[0], [
                        (item, C_MUTED, False),
                    ], 9.5, space_after_pt=2, line_spacing=1.15)
                    warn_y += 180000

    return detail_page_offset


def run(manifest_path, output_pptx):
    # manifest 缺失与 manifest 为空是两件完全不同的事，必须分开处理：
    # 前者是上游坏了（exit 2 便于调用方区分），后者是"无差异"这个有效结论（exit 0）。
    if not os.path.exists(manifest_path):
        print(f"Error: manifest not found: {manifest_path}")
        print("The upstream step did not produce a manifest; check that the render step succeeded.")
        sys.exit(2)

    with open(manifest_path, "r", encoding="utf-8") as f:
        manifest = json.load(f)

    prs = Presentation()
    prs.slide_width = SLIDE_WIDTH_EMU
    prs.slide_height = SLIDE_HEIGHT_EMU

    base_dir = os.path.dirname(manifest_path)
    summary = _load_summary(manifest_path)

    if not manifest:
        # render_summary.json 由 render_diff 在无差异时同步写出
        summary_path = os.path.join(base_dir, "render_summary.json")
        if not summary and os.path.exists(summary_path):
            with open(summary_path, "r", encoding="utf-8") as f:
                summary = json.load(f)
        build_empty_slide(prs, summary)
        prs.save(output_pptx)
        print(f"PPTX saved: {output_pptx}")
        print("No geometric differences detected; wrote an explanatory slide "
              "(a valid result, not a failure)")
        return

    # ===== 汇总页 =====
    detail_page_offset = build_summary_page(prs, manifest, summary)
    # 详情页上限截断：按体积变化占比降序取前 N
    sorted_manifest = sorted(manifest, key=lambda e: abs(e.get("volume_delta_pct") or 0), reverse=True)
    if len(sorted_manifest) > DETAIL_PAGE_LIMIT:
        detail_manifest = sorted_manifest[:DETAIL_PAGE_LIMIT]
        print(f"Detail slides truncated: {len(manifest)} differences found, "
              f"showing only the top {DETAIL_PAGE_LIMIT} (by importance)")
    else:
        detail_manifest = sorted_manifest

    content_w = SLIDE_WIDTH_EMU - 2 * MARGIN_EMU
    col_w = (content_w - 2 * GAP_EMU) // 3
    img_area_top = MARGIN_EMU + TITLE_BAR_H + CAPTION_H
    img_area_h = SLIDE_HEIGHT_EMU - img_area_top - BAND_H - MARGIN_EMU - GAP_EMU

    for idx, entry in enumerate(detail_manifest):
        slide = prs.slides.add_slide(prs.slide_layouts[6])  # blank layout
        is_moved = entry.get("change_type") == "moved"

        # ============ 顶部标题条 ============
        # 左侧品红竖条 + 零件名（大号，带下划线）+ 下一行体积变化（小号）。
        # 【不要在标题条下方画整幅宽的分隔线】曾在 MARGIN_EMU+TITLE_BAR_H-150000 处画过一条，
        # 那个位置正好压在第二行文字的下缘上，把"零件体积 / 变化量"那行遮住了（用户反馈）。
        # 标题的视觉收边改由零件名自身的下划线承担，不占额外垂直空间，也不可能压到别的文字。
        bar_w = 46000
        add_rect(slide, MARGIN_EMU, MARGIN_EMU, bar_w, TITLE_BAR_H - 180000,
                 fill=C_ACCENT)
        tf = new_textbox(slide, MARGIN_EMU + bar_w + 130000, MARGIN_EMU,
                         content_w * 0.55, TITLE_BAR_H - 180000)
        p = tf.paragraphs[0]
        put_runs(p, [(entry["base_name"], C_TITLE, True)], 22, space_after_pt=3,
                 underline=True)
        p2 = tf.add_paragraph()
        if is_moved:
            # 位移类不显示"变化量 mm³"——它恒为 0，写出来会被读成"什么都没改"
            put_runs(p2, [
                (i18n.t("pptx.title.counter", index=idx + 1, total=len(detail_manifest)),
                 C_MUTED, False),
                ("      " + i18n.t("pptx.col.type") + "  ", C_LABEL, False),
                (i18n.t("pptx.title.pose_change"), C_ACCENT, True),
                ("      " + i18n.t("pptx.field.part_volume") + "  ", C_LABEL, False),
                (i18n.t("pptx.title.volume_unchanged",
                        volume=fmt(entry.get('new_volume'))), C_VALUE, True),
            ], 11.5, space_after_pt=0)
        else:
            put_runs(p2, [
                (i18n.t("pptx.title.counter", index=idx + 1, total=len(detail_manifest)),
                 C_MUTED, False),
                ("      " + i18n.t("pptx.field.part_volume") + "  ", C_LABEL, False),
                (f"{fmt(entry.get('old_volume'))} → {fmt(entry.get('new_volume'))} mm³",
                 C_VALUE, True),
                ("      " + i18n.t("pptx.title.delta") + "  ", C_LABEL, False),
                (f"{fmt(entry.get('volume_delta'))} mm³ "
                 f"({fmt(entry.get('volume_delta_pct'))}%)", C_ACCENT, True),
            ], 11.5, space_after_pt=0)

        # ============ 三图横排 ============
        for i, key in enumerate(PANELS):
            left_base = MARGIN_EMU + i * (col_w + GAP_EMU)

            img_filename = entry.get(key, "")
            if not img_filename:
                continue
            img_path = os.path.join(base_dir, img_filename)
            if not os.path.exists(img_path):
                continue

            img_w, img_h = get_image_size(img_path)
            aspect = img_w / img_h
            if aspect > col_w / img_area_h:
                pic_w = col_w
                pic_h = int(col_w / aspect)
            else:
                pic_h = img_area_h
                pic_w = int(img_area_h * aspect)

            left = left_base + (col_w - pic_w) // 2
            top = img_area_top + (img_area_h - pic_h) // 2
            pic = slide.shapes.add_picture(img_path, left, top, pic_w, pic_h)
            # 给图加一圈浅边框，把白底图和白底页面分开
            pic.line.color.rgb = C_RULE
            pic.line.width = Pt(0.75)

        # ============ 底部信息带 ============
        band_top = SLIDE_HEIGHT_EMU - BAND_H - MARGIN_EMU // 2
        add_rect(slide, MARGIN_EMU, band_top, content_w, BAND_H,
                 fill=C_BAND_BG, line=C_RULE)

        inner_pad = 170000
        inner_top = band_top + 130000
        # 文字区高度要扣掉图例行，否则右栏文案一换行就把图例挤出页面下沿（已实测第1页如此）
        inner_h = BAND_H - 130000 - LEGEND_H
        # 左栏只放三个短数值，右栏要放会换行的长描述，故右栏给更大宽度
        left_col_w = int(content_w * 0.33)
        right_col_x = MARGIN_EMU + inner_pad + left_col_w + 180000
        right_col_w = content_w - inner_pad * 2 - left_col_w - 260000

        # ---- 左栏：可量化的数值 ----
        tf = new_textbox(slide, MARGIN_EMU + inner_pad, inner_top,
                         left_col_w, inner_h)
        if is_moved:
            # 位移类：报位移量与旋转角，【不显示增料/减料行】（恒为 0，显示出来会
            # 被误读成"没改动"）。每项独占一段——左栏只有 33% 宽度，挤一行会换行错位（坑 G7）。
            rot = entry.get("rotation_deg") or 0
            fields = [(i18n.t("pptx.field.translation"),
                       f"{fmt(entry.get('translation_mm'))} mm", C_ACCENT)]
            if rot > 0:
                by = entry.get("rotation_detected_by")
                suffix = (i18n.t("pptx.value.rotation_bbox_note")
                          if by == "bbox_permutation" else "")
                fields.append((i18n.t("pptx.field.rotation"),
                               f"{fmt(rot, 1)}°{suffix}", C_ACCENT))
            else:
                fields.append((i18n.t("pptx.field.rotation"),
                               i18n.t("pptx.value.rotation_none"), C_MUTED))
            fields.append((i18n.t("pptx.field.part_volume"),
                           f"{fmt(entry.get('new_volume'))} mm³", C_VALUE))
            add_field_lines(tf, fields, 12.5)
        else:
            rem = entry.get("removed_volume") or 0
            add_v = entry.get("added_volume") or 0
            # 增/减料按实际有无着色：为0时用灰色，避免"绿色0.00"误导成有增料
            size_lines = [(lab, val, C_VALUE, ind)
                          for lab, val, ind in cluster_size_lines(entry)]
            add_field_lines(tf, size_lines + [
                (i18n.t("pptx.field.removed_material"), f"{fmt(rem)} mm³",
                 C_REMOVE if rem > 0 else C_MUTED),
                (i18n.t("pptx.field.added_material"), f"{fmt(add_v)} mm³",
                 C_ADD if add_v > 0 else C_MUTED),
            ], 12.5)

        # ---- 右栏：定性描述 ----
        if is_moved:
            desc = i18n.t("pptx.desc.moved")
        else:
            # 纯增料/纯减料时，新版特写图本来就没有品红——这是正确结果而非渲染失败。
            desc = i18n.t("pptx.desc.no_magenta")
        view_tag = entry.get("overview_view_tag")
        vd = entry.get("overview_view_dir")
        if view_tag and vd:
            view_str = f"{view_tag}  ({vd[0]:+.2f}, {vd[1]:+.2f}, {vd[2]:+.2f})"
        else:
            view_str = "—"
        # 视角说明并入该行同段的灰色小注，不再另起一段——另起段落时长文案换行会顶穿信息带。
        if entry.get("overview_view_fallback"):
            # 该差异藏在装配体内部，任何"看得懂"的视角都拍不到高亮。必须写明，
            # 否则读者会以为整体图渲染错了或差异找错了。
            note = i18n.t("pptx.note.view_fallback")
        else:
            note = i18n.t("pptx.note.view_auto")
        tf = new_textbox(slide, right_col_x, inner_top, right_col_w, inner_h)
        add_field_lines(tf, [
            (i18n.t("pptx.field.change_note"), desc, C_VALUE),
        ], 12.5)
        p = tf.add_paragraph()
        put_runs(p, [
            ("▪  ", C_ACCENT, True),
            (i18n.t("pptx.field.overview_view"), C_LABEL, False),
            ("    ", C_LABEL, False),
            (view_str, C_VALUE, True),
            (note, C_MUTED, False),
        ], 12.5, space_after_pt=0, line_spacing=1.15)

        # ---- 底部图例（跨整条带宽，占独立一行不与上方文字重叠）----
        tf = new_textbox(slide, MARGIN_EMU + inner_pad,
                         band_top + BAND_H - LEGEND_H,
                         content_w - inner_pad * 2, LEGEND_H,
                         anchor=MSO_ANCHOR.MIDDLE)
        p = tf.paragraphs[0]
        if is_moved:
            # 位移类的图例必须换文案：品红在这类图上代表零件本体，不是"改动的材料"，
            # 沿用形状类图例会直接说反。
            put_runs(p, [
                (i18n.t("pptx.legend.label") + "  ", C_LABEL, True),
                ("■ ", C_ACCENT, True),
                (i18n.t("pptx.legend.moved_magenta"), C_MUTED, False),
                ("        ", C_MUTED, False),
                ("→ ", RGBColor(0x00, 0x5A, 0xC8), True),
                (i18n.t("pptx.legend.moved_arrow"), C_MUTED, False),
                ("        ", C_MUTED, False),
                ("↗ ", C_MUTED, True),
                (i18n.t("pptx.legend.axes_moved"), C_MUTED, False),
            ], 9.5, space_after_pt=0)
        else:
            put_runs(p, [
                (i18n.t("pptx.legend.label") + "  ", C_LABEL, True),
                ("■ ", C_ACCENT, True),
                (i18n.t("pptx.legend.shape_magenta"), C_MUTED, False),
                ("        ", C_MUTED, False),
                ("▭ ", RGBColor(0xE0, 0x00, 0x00), True),
                # 文案必须短：图例行只有 LEGEND_H 一行高度，实测写成
                # "红框＝各处差异分别定位（①②… 对应左栏尺寸清单）" 会把整行挤成两行、
                # 末尾"（X红 / Y绿 / Z蓝）"溢出到第二行被裁（坑 G1）。
                (i18n.t("pptx.legend.shape_redbox"), C_MUTED, False),
                ("        ", C_MUTED, False),
                ("↗ ", C_MUTED, True),
                (i18n.t("pptx.legend.axes_shape"), C_MUTED, False),
            ], 9.5, space_after_pt=0)

    prs.save(output_pptx)
    print(f"PPTX saved: {output_pptx}")
    print(f"Total {detail_page_offset + len(detail_manifest)} slides "
          f"({detail_page_offset} summary + {len(detail_manifest)} detail)")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Build the comparison PPTX from a render manifest")
    ap.add_argument("render_manifest")
    ap.add_argument("output_pptx")
    ap.add_argument("--lang", default=None, choices=i18n.available_langs(),
                    help="Language of the generated slides (default: en)")
    args = ap.parse_args()
    i18n.set_lang(args.lang)
    run(args.render_manifest, args.output_pptx)
