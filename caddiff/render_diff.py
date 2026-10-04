r"""
渲染差异截图：对每个几何差异生成整体定位图 + 新旧版本同角度特写图，标注红色矩形框，
并把差异几何体本身用品红色不透明叠加渲染（红框只起辅助定位作用；差异体积多在零件自身
体量的1%左右，仅靠框住一片灰色区域人眼分辨不出改动，颜色高亮才能让改动本身一眼可见）。

必须用FreeCAD自带Python解释器运行：
    <FreeCAD>\bin\python.exe render_diff.py <geom_diff.json> <stp_old> <stp_new> <output_dir>

【核心执行顺序约束，改本文件前必读】
run() 严格分三阶段：预算(precompute) -> 注入(inject) -> 渲染(render)，不得打乱。
这不是可随意调整的代码组织方式，而是 FreeCAD 的硬约束：App::Part 容器的 .Shape 属性
一旦被任何一次 Visibility 写操作触及就会永久消失（hasattr 由 True 变 False，
且把 Visibility 改回 True 也不恢复），所以全部几何读取必须严格早于全部可见性写入。
已实测：本装配体的 PART-A-703 在新版被 Creo 重构成了 App::Part 容器，此前代码在
渲染循环内部才取 Shape，于是第1轮渲染整体图（给全部几何对象写 Visibility=True）就摘掉了
它的 .Shape，第3轮再取只能拿到 None —— 高亮体静默缺失、产出的特写图 0 个品红像素却不报错。

【整体定位图的视角是自动选出来的，不是固定 isometric】
整体图的职责是"让读者看清差异点在装配体的哪个部位"。若与特写图同用等轴视角，差异高亮
多半被自身或兄弟零件挡住，这张图的信息量就归零了（实测 PART-C 原本 0 个品红）。
现在的做法是：非目标零件设 CONTEXT_TRANSPARENCY=85，然后扫描 26 个候选视线方向，
按"品红可见性 × 画面可读性"综合评分自动选最优（见 pick_best_direction / score_direction）。
因此三张图不再同视角，每张图顶部都加了方向标记带（add_direction_banner）说明各自视角。
"""
import json
import sys
import os
import time
from collections import defaultdict

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
if SCRIPT_DIR not in sys.path:
    sys.path.insert(0, SCRIPT_DIR)

import console  # noqa: E402
import fcenv  # noqa: E402  （路径解析的唯一真相源，必须先于 import FreeCAD）
import i18n  # noqa: E402
from i18n import t  # noqa: E402

console.enable_utf8_output()

# FreeCAD 模块的 bin/lib 由解释器自身位置推导；找不到就不注入（Linux 在系统 site-packages 里）。
fcenv.inject_freecad_paths()

import FreeCAD  # noqa: E402
import FreeCADGui  # noqa: E402
import Import  # noqa: E402
from FreeCAD import Vector  # noqa: E402

# 渲染参数
# 注：FreeCAD 3D视口widget的原生像素尺寸是固定的（约603x450，不随主窗口resize/maximize变化），
# av.getPointOnScreen()返回的坐标系统就是这个原生尺寸，与saveImage()传入的分辨率无关。
# 必须用av.getSize()拿到的原生尺寸截图，才能保证投影坐标与图片像素对齐；
# 再用Pillow等比放大到UPSCALE_FACTOR倍以保证清晰度，放大时红框坐标同比例缩放。
UPSCALE_FACTOR = 3
RECT_COLOR = (255, 0, 0)  # 红色（辅助定位框）
# 描边宽度必须按图宽等比推导，不能写死像素——与字号那个坑（见 BANNER_*_RATIO / F7）同源。
# 实测换算：原生视口 603px × UPSCALE_FACTOR=3 = 1809px 宽的图，在 PPT 三联版式里
# 每张显示 4.043 英寸（受列宽限制），即显示 DPI ≈ 447。旧的写死值 3px 只有 0.48pt，
# 用户反馈"太细看不清"。0.0069 → 12.4px → 2.0pt，清晰可辨且不至于遮挡差异
# （描边是向外扩展的，见 draw_rects_on_image）。
RECT_WIDTH_RATIO = 0.0069
RECT_MIN_WIDTH = 3           # 极小图的兜底下限
PADDING_PX = 20  # 框与bbox边缘的像素留白（放大后坐标系下）

# 差异簇（对称差几何体中空间分离的各个实体）参数。
# why 要分簇：对称差常由多块空间分离的材料构成，只画它们的并集 bbox 会退化成
# "一个大框套住整个零件"，框就失去定位意义。实测 PART-A 的增料由两块构成，
# 中心 x 分别为 +17.89 与 -0.38（相距 18.3mm），而零件本身仅约 19mm 宽——
# 并集 bbox 19.17×10.97×3.21mm 正好等于整个零件，而撑大它的第二块只有 0.76mm³。
CLUSTER_MIN_VOLUME = 0.001   # 与 geom_diff.VOLUME_THRESHOLD 同值：布尔碎片不单独画框
MAX_RECTS = 8                # 单图最多画几个框；超限按体积降序取前 N 并打印被丢弃项
RECT_LABEL_RATIO = 0.030     # 框编号字号占图宽比例（同样不写死像素）
RECT_LABEL_GAP_RATIO = 0.022 # 标签与框边的间距占图宽比例（引出线长度由它决定）
# 圈号字符序列。build_pptx.CIRCLED 必须与此一致，否则 PPT 左栏的尺寸清单
# 对不上图上的框标注（读者无法把"① 1.29×..."对应到具体哪个框）。
CIRCLED_DIGITS = "①②③④⑤⑥⑦⑧"
HIGHLIGHT_COLOR = (1.0, 0.0, 1.0)  # 品红色（差异几何体本身，0~1浮点，FreeCAD ShapeColor格式）
TARGET_TRANSPARENCY = 40  # 目标零件本身设为半透明，避免遮住藏在内部/边缘的差异高亮体

# 整体定位图里【非目标零件】的透明度。整体图的职责是"暴露差异点在装配体中的位置"，
# 若周围零件不透明，差异高亮体会被兄弟零件挡死。实测26方向×4档透明度（原生视口像素）：
#   零件            ctx=0    ctx=50   ctx=70        ctx=85
#   PART-C 0/26方向 0/26     3/26(165px)   7/26(267px)   ← 只有85能救回来
#   PART-D        19/26    19/26    19/26         19/26
#   PART-A         26/26    26/26    26/26         26/26
# 取85：它把"整体图0品红"从既定限制变成了可解问题，且对本来可见的两处无损。
CONTEXT_TRANSPARENCY = 85

# 整体图的候选视线方向：26个（6面 + 12棱 + 8角）。逐差异扫描全部方向、按综合评分选最优，
# 替代此前固定的 viewIsometric()——固定视角与特写图同向，差异点常被自身零件挡住。
# 实测单方向约0.85s（setViewDirection + fitAll + saveImage + 像素统计），26方向约22s/差异。
VIEW_DIRECTIONS = [
    (f"{x:+d}{y:+d}{z:+d}",
     (x / (x * x + y * y + z * z) ** 0.5,
      y / (x * x + y * y + z * z) ** 0.5,
      z / (x * x + y * y + z * z) ** 0.5))
    for x in (-1, 0, 1) for y in (-1, 0, 1) for z in (-1, 0, 1)
    if not (x == 0 and y == 0 and z == 0)
]

# 方向评分参数（实测定标，见 score_direction 的说明）
SCORE_SATURATED_FULL = 400.0   # 全饱和品红达到该像素数即视为"充分可见"，再多不加分
SCORE_BODY_FULL = 0.06         # 灰色实体面积占比达到该值即视为"画面有实体感"
SCORE_SOLIDITY_FLOOR = 0.15    # 紧凑度下限，避免0分导致整个候选被一票否决

# 可读性最低门槛。低于此值的方向即使品红最多也不选——那种图是线框糊团，
# 装配体认不出来，放进PPT读者看不懂，等于没有定位价值。
# 实测：PART-C 唯一能看到品红的方向 -1+0+0 可读性仅 0.0385（实体占比0.028、
# 品红碎成16块），而它在其他方向可读性有 0.65 但完全看不到品红（被兄弟零件挡死）。
# 此时正确取舍是"要一张看得懂的定位图 + 红框指位置"，而不是"要一张看不懂但有品红的图"，
# 因为整体图的职责本就是定位，差异细节由两张特写图承担。
SCORE_READABILITY_FLOOR = 0.25

# 可见性最低门槛（原生视口像素数）。低于此值等于肉眼看不见，不能算"看得到差异"。
# 实测：PART-C 在 -1+1+0 方向只有 1 个全饱和品红像素——数值上大于 0，
# 但在 603x450 的原生视口里就是一个点，读者根本看不见。若门槛只写"非零"，
# 这种方向会挤掉真正的 fallback 判定，产出一张"看着什么都没有却不作任何说明"的图。
# 30px 对应放大 3 倍后约 270px 的可见色块，是能被注意到的最小量级。
SCORE_MIN_VISIBLE_PX = 30

# 构图指标：内容像素分布的离心率（主轴/次轴标准差之比）上限。
# why 用离心率而不是"内容外接框填充率"：填充率实测反向、无区分度——斜躺视角 0.398、
# 等轴基准 0.656，按填充率排反而选中斜躺的。离心率才真正区分"一团"与"斜躺一条"：
# 实测该装配体 26 个方向，斜躺的四个方向 ecc=2.41~2.50，其余全在 1.04~2.02，
# 等轴基准 1.51。取 2.2 作上限，正好把斜躺那一档排除掉。
SCORE_ECC_LIMIT = 2.2

# 方向标记带（贴在每张图顶部，让读者一眼看出三张图的视角差异）
# 【字号必须按图宽等比推导，不能写死像素】三张图在 PPT 里每张只占约 4 英寸宽，
# 1827px 的图显示 DPI 约 450 —— 曾按固定像素写死（标题40px/副行27px/轴标24px），
# 实际显示只有 6.4pt/4.3pt/3.9pt，用户反馈"太小看不清"。
# 比例值经 PowerPoint 导出图目视调定：标题 6.2% 图宽 ≈ 18pt、副行 4.2% ≈ 12pt，
# 在三联图版式下清晰可读（换分辨率也不会失配）。
BANNER_TITLE_RATIO = 0.062
BANNER_SUB_RATIO = 0.042
BANNER_AXIS_RATIO = 0.040
BANNER_PAD_RATIO = 0.018          # 内边距占图宽比例
BANNER_BG = (250, 250, 252)
BANNER_FG = (28, 28, 32)
BANNER_SUB_FG = (95, 95, 105)
BANNER_LINE = (150, 150, 160)
BANNER_ACCENT = (192, 0, 96)      # 标题左侧色条，与品红呼应
AXIS_COLORS = {"X": (200, 30, 30), "Y": (30, 150, 30), "Z": (30, 60, 200)}
AXIS_GIZMO_RATIO = 0.058          # 坐标轴指示器半径占图宽比例
# 中文字体候选。全部未命中时会退回 load_default() 出豆腐块，且【不报错】——
# 这是典型的"不报错但产物错"，故 load_font 在全空时打印显著警告（见该函数）。
# Linux 路径是为线上部署（容器里没有 C:\Windows\Fonts）准备的。
FONT_CANDIDATES = (r"C:\Windows\Fonts\msyh.ttc", r"C:\Windows\Fonts\simhei.ttf",
                   r"C:\Windows\Fonts\simsun.ttc",
                   "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
                   "/usr/share/fonts/opentype/noto/NotoSansCJKsc-Regular.otf",
                   "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
                   "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc",
                   "/usr/share/fonts/truetype/wqy/wqy-microhei.ttc",
                   "/usr/share/fonts/truetype/arphic/uming.ttc",
                   "/System/Library/Fonts/PingFang.ttc")

# 位移类差异（change_type == "moved"）的呈现参数。
# 该类差异没有对称差几何体，"品红高亮 + 红框"那套不适用：品红在这里代表
# "整体发生位移的零件本身"，语义由标记带与 PPT 文字带说明，不靠读者猜。
MOVE_ARROW_COLOR = (0, 90, 200)   # 位移箭头用蓝色，与品红高亮/红框都不撞色
MOVE_ARROW_WIDTH_RATIO = 0.0055
MOVE_ARROW_HEAD_RATIO = 0.020
MOVE_LABEL_RATIO = 0.034


# 每个App::Part装配体自带一套Origin基准几何(X/Y/Z轴+XY/XZ/YZ基准面)，默认Visibility=True。
# fitAll()会把这些延伸很远的基准几何也计入取景范围，导致真正的模型被挤压成画面中的一个小点，
# 且坐标轴/半透明基准面会盖满整个画面。渲染时必须排除这些类型，只显示真实几何。
NON_GEOMETRY_TYPES = ("App::Origin", "App::Line", "App::Plane", "App::Point")


def base_name(name):
    last_alpha = max((i for i, c in enumerate(name) if c.isalpha()), default=len(name) - 1)
    return name[: last_alpha + 1]


def is_real_geometry(obj):
    return obj.TypeId not in NON_GEOMETRY_TYPES


def project_bbox(av, bbox_dict, flip_y_height=None):
    """把3D bbox的8个角投影到2D屏幕坐标，返回外接矩形(min_x,min_y,max_x,max_y)。

    flip_y_height: 传入视口高度时，把 y 从"下原点"翻转为图像的"上原点"。

    【y轴方向这个坑极难发现，改这里前务必读完】
    av.getPointOnScreen() 采用 OpenGL/Coin3D 惯例：原点在视口左下角、y 轴向上；
    而 Pillow 图像的原点在左上角、y 轴向下。两者相差一次翻转 y' = 视口高 - y。

    为什么这个错误能长期潜伏：只要取景是 fitAll（模型居中），投影出的 y 区间就几乎
    对称于视口中线（实测三处分别为 36..414、25..425、74..375，上下界之和都≈视口高450），
    翻转前后数值几乎一样，红框看起来是对的。只有 boxZoom 聚焦到偏离画面中心的局部时
    才会暴露——实测偏移量随聚焦程度放大（不聚焦7px、聚焦30%边距时46px）。
    翻转后三档取景的品红全部落入红框内（余量2~7px），未翻转时上沿溢出7~46px。
    """
    corners = [
        Vector(bbox_dict["x_min"], bbox_dict["y_min"], bbox_dict["z_min"]),
        Vector(bbox_dict["x_min"], bbox_dict["y_min"], bbox_dict["z_max"]),
        Vector(bbox_dict["x_min"], bbox_dict["y_max"], bbox_dict["z_min"]),
        Vector(bbox_dict["x_min"], bbox_dict["y_max"], bbox_dict["z_max"]),
        Vector(bbox_dict["x_max"], bbox_dict["y_min"], bbox_dict["z_min"]),
        Vector(bbox_dict["x_max"], bbox_dict["y_min"], bbox_dict["z_max"]),
        Vector(bbox_dict["x_max"], bbox_dict["y_max"], bbox_dict["z_min"]),
        Vector(bbox_dict["x_max"], bbox_dict["y_max"], bbox_dict["z_max"]),
    ]
    screen_pts = []
    for c in corners:
        pt = av.getPointOnScreen(c)
        screen_pts.append((pt[0], pt[1]))
    xs = [p[0] for p in screen_pts]
    ys = [p[1] for p in screen_pts]
    if flip_y_height is not None:
        ys = [flip_y_height - y for y in ys]
    return min(xs), min(ys), max(xs), max(ys)


def save_native_and_upscale(av, path, size=None):
    """按指定尺寸截图，再等比放大清晰度。size为None时取当前视口尺寸。
    一般不要直接调用——用 capture_and_project() 保证截图与投影同源。"""
    from PIL import Image
    w, h = size if size is not None else av.getSize()
    av.saveImage(path, w, h, 'White')
    img = Image.open(path)
    img = img.resize((w * UPSCALE_FACTOR, h * UPSCALE_FACTOR), Image.LANCZOS)
    img.save(path)
    return w, h


def capture_and_project(av, path, bbox):
    """原子操作：在同一时刻取视口尺寸，用它截图，并用同一坐标系投影bbox（含y轴翻转）。

    why（本文件最容易踩、症状最具误导性的两个坑，都在这里统一处理）：
    1. av.getSize() 返回的视口尺寸会在运行过程中变化（已实测 600→603→609），而
       av.getPointOnScreen() 给出的坐标相对"调用当时"的视口，av.saveImage(path,w,h)
       又会严格按传入尺寸出图并线性缩放画面。三者不同源则红框与画面水平错位。
       所以尺寸只取一次，截图与投影共用，中间不插入 fitAll/boxZoom/updateGui。
    2. getPointOnScreen 是下原点(y向上)，图像是上原点(y向下)，必须翻转 y。
       详见 project_bbox 的说明。
    """
    size = av.getSize()
    save_native_and_upscale(av, path, size)
    bbox_2d = project_bbox(av, bbox, flip_y_height=size[1])
    after = av.getSize()
    if tuple(after) != tuple(size):
        # 截图与投影之间尺寸仍发生了变化，说明有别的调用触发了重算，必须暴露出来
        print(f"    !! Viewport size changed between capture and projection "
              f"{size} -> {after}; red rects may be misaligned")
    return size, bbox_2d


def check_image_nonblank(img_path):
    """检测图片是否完全空白（无任何非白色像素）。用于捕获"父级未设可见→渲染出空图"这类隐性失败。"""
    from PIL import Image
    img = Image.open(img_path).convert("RGB")
    extrema = img.getextrema()
    # 三通道都是(255,255)说明全白
    return not all(lo == 255 and hi == 255 for lo, hi in extrema)


def count_magenta(img_path):
    """统计品红色高亮像素数。
    why：bug A(App::Part.Shape失效) 和 bug B(前轮高亮体残留) 都是"代码不报错、产物是错的"，
    仅靠人工看图漏了整整一轮。把品红像素数落进manifest，下次异常一眼可见，不必再写诊断脚本。
    """
    from PIL import Image
    img = Image.open(img_path).convert("RGB")
    px = img.load()
    w, h = img.size
    cnt = 0
    for y in range(h):
        for x in range(w):
            r, g, b = px[x, y]
            if r > 150 and b > 150 and g < 110:
                cnt += 1
    return cnt


def magenta_masks(img_path):
    """返回 (全饱和品红mask, 低饱和混色mask)，用numpy一次算完。

    【为什么必须区分这两者】高亮体本身是全饱和品红，实测 (203,17,215)/(219,17,218)，g<30；
    而半透明零件"透出"的高亮呈低饱和紫，实测 (182,112,205)，g在110~190。
    后者是赝像：它会让"品红溢出红框"的判据产生假失败——实测把非目标零件设为
    CONTEXT_TRANSPARENCY=85 后，若按 g<110 统计会看到品红落在红框外，
    但按 g<60 统计则框外恒为 0，说明溢出的全是混色赝像而非真高亮。
    所以位置类断言与方向评分一律只认全饱和 mask。
    """
    import numpy as np
    from PIL import Image
    arr = np.asarray(Image.open(img_path).convert("RGB")).astype(np.int16)
    r, g, b = arr[:, :, 0], arr[:, :, 1], arr[:, :, 2]
    saturated = (r > 150) & (b > 150) & (g < 60)
    loose = (r > 150) & (b > 150) & (g < 110)
    return saturated, (loose & ~saturated), arr


def largest_component_ratio(mask):
    """最大连通块占整个mask的比例（4邻域）。用于衡量高亮是"一块完整区域"还是"碎成一片"。

    why：正交视角下大量共面边挤在一起，高亮体被前景线框切碎——实测正侧视方向的品红
    碎成16个连通块（占比0.135），而好的斜视角是1~4块（占比0.8~1.0）。
    碎块多说明画面糊成一团，这种图放进PPT读者看不懂，所以要在选向时惩罚它。
    不用 scipy（FreeCAD 的 Python 未打包 scipy），纯 numpy + 栈式 BFS。
    """
    import numpy as np
    total = int(mask.sum())
    if total == 0:
        return 0.0, 0
    h, w = mask.shape
    seen = np.zeros_like(mask)
    biggest = 0
    n_cc = 0
    for sy, sx in np.argwhere(mask):
        if seen[sy, sx]:
            continue
        n_cc += 1
        size = 0
        stack = [(int(sy), int(sx))]
        seen[sy, sx] = True
        while stack:
            y, x = stack.pop()
            size += 1
            for dy, dx in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                ny, nx = y + dy, x + dx
                if 0 <= ny < h and 0 <= nx < w and mask[ny, nx] and not seen[ny, nx]:
                    seen[ny, nx] = True
                    stack.append((ny, nx))
        biggest = max(biggest, size)
    return biggest / total, n_cc


def score_direction(img_path, rect, n_zero_components, pad=8):
    """给一个候选视线方向打分，返回 (综合分, 明细dict)。

    综合分 = 可见分 × 可读性分，两者都必须好才算好方向。

    【为什么不能只按品红像素数排序——这是实测踩出来的】
    只按像素数选，PART-C 会选出正交侧视 -1+0+0：品红确实最多（156px），
    但画面退化成线框糊团（灰色实体面积占比仅0.028、品红碎成16个连通块），
    装配体根本认不出来，违背"整体视角"的用意。加入可读性因子后，实测三处差异里
    有两处改选了不同方向（PART-D: -1-1+0 → -1+0+1，PART-A: -1-1-1 → -1+1+1），
    目视确认新选择的画面立体可辨且品红清晰。

    各因子（阈值均为实测定标）：
      可见分   = min(1, 框内全饱和品红 / SCORE_SATURATED_FULL)
      立体分   = 视线方向零分量个数 → 0个(角视角)=1.0 / 1个(棱)=0.8 / 2个(正交面)=0.55
                 正交视角共面边重合最严重，故惩罚最重
      紧凑分   = 最大连通块占比（下限 SCORE_SOLIDITY_FLOOR）
      体量分   = min(1, 灰色实体面积占比 / SCORE_BODY_FULL)
      构图分   = 内容像素分布的离心率是否在 SCORE_ECC_LIMIT 以内。
                 fitAll 后模型总是居中，但"斜着躺"的视角会让装配体只占画面对角线上一条带、
                 四周大片空白，读者看着模型很小。实测该装配体斜躺的四个方向 ecc=2.41~2.50，
                 其余方向 1.04~2.02（等轴基准 1.51），故超限直接判 0 分而不是打折——
                 这类构图是明确不可接受，不是"稍差一点"。
    """
    import numpy as np
    saturated, mixed, arr = magenta_masks(img_path)
    h, w = saturated.shape
    x1, y1, x2, y2 = [int(v) for v in rect]
    inbox = np.zeros_like(saturated)
    inbox[max(0, y1 - pad):min(h, y2 + pad + 1),
          max(0, x1 - pad):min(w, x2 + pad + 1)] = True

    sat_in = int((saturated & inbox).sum())
    sat_out = int((saturated & ~inbox).sum())
    # 灰色实体：非白、非暗线、非高亮 —— 代表"看得见的零件表面"
    nonwhite = arr.sum(axis=2) < 730
    dark = arr.max(axis=2) < 120
    body_pct = float((nonwhite & ~dark & ~saturated & ~mixed).sum()) / (h * w)

    # 构图：内容像素分布的离心率（主轴/次轴标准差之比）
    ys, xs = np.nonzero(nonwhite)
    if len(xs) >= 10:
        cov = np.cov(np.stack([xs.astype(float), ys.astype(float)]))
        ev = np.clip(np.linalg.eigvalsh(cov), 1e-9, None)
        ecc = float((ev[1] / ev[0]) ** 0.5)
    else:
        ecc = 99.0
    compose = 1.0 if ecc <= SCORE_ECC_LIMIT else 0.0

    solidity, n_cc = largest_component_ratio(saturated & inbox)
    visible = min(1.0, sat_in / SCORE_SATURATED_FULL)
    solid_factor = {0: 1.0, 1: 0.8, 2: 0.55}.get(n_zero_components, 0.55)
    volume_factor = min(1.0, body_pct / SCORE_BODY_FULL)
    readability = (solid_factor * max(solidity, SCORE_SOLIDITY_FLOOR)
                   * volume_factor * compose)
    return visible * readability, {
        "sat_in": sat_in, "sat_out": sat_out, "n_cc": n_cc,
        "solidity": round(solidity, 3), "body_pct": round(body_pct, 4),
        "ecc": round(ecc, 3), "compose": compose,
        "visible": round(visible, 3), "readability": round(readability, 4),
    }


_FONT_WARNED = False


def load_font(size):
    from PIL import ImageFont
    for path in FONT_CANDIDATES:
        if os.path.exists(path):
            try:
                return ImageFont.truetype(path, size)
            except Exception:
                continue
    # 全部候选未命中：退回位图默认字体会把中文渲染成豆腐块，而且【不报错】。
    # 静默出豆腐块图是典型的"不报错但产物错"，必须显式告警（每次运行只打一次）。
    global _FONT_WARNED
    if not _FONT_WARNED:
        _FONT_WARNED = True
        print("  !! WARNING: no font from FONT_CANDIDATES was found; in-image text "
              "will render as tofu boxes. Install a CJK font or add this machine's "
              "font path to FONT_CANDIDATES.")
    from PIL import ImageFont as IF
    return IF.load_default()



def axis_screen_dirs(av):
    """把世界坐标三轴投影到屏幕，算出各轴在图像上的方向单位向量（已翻转为上原点）。

    用途：给每张图画坐标轴指示器。用 getPointOnScreen 实测投影而非自己推导相机矩阵，
    与红框坐标同源，不会出现"标记说的方向和画面实际方向不一致"。
    返回 {"X": (dx,dy), ...}，dy 已按图像坐标系（y向下）翻转。
    """
    origin = Vector(0, 0, 0)
    p0 = av.getPointOnScreen(origin)
    out = {}
    for name, vec in (("X", Vector(10, 0, 0)), ("Y", Vector(0, 10, 0)), ("Z", Vector(0, 0, 10))):
        p = av.getPointOnScreen(vec)
        dx = p[0] - p0[0]
        dy = -(p[1] - p0[1])   # getPointOnScreen是下原点，图像是上原点，取反
        norm = (dx * dx + dy * dy) ** 0.5
        out[name] = (dx / norm, dy / norm) if norm > 1e-6 else (0.0, 0.0)
    return out


def banner_height(img_width):
    """标记带高度：按图宽等比推导，与字号同源，保证两者永不失配。
    返回值会写进 manifest 的 banner_h 字段——下游（验证脚本等）读它，不要自己写死常量。"""
    title = int(img_width * BANNER_TITLE_RATIO)
    sub = int(img_width * BANNER_SUB_RATIO)
    pad = int(img_width * BANNER_PAD_RATIO)
    return pad * 2 + title + sub + int(sub * 0.75)


def _fit_text(draw, text, font, max_w, keep=4):
    """把文本截断到 ``max_w`` 像素以内，超出部分换成省略号。

    why 按**实测像素宽度**而不是字符数：同一字符数在中英文下宽度差近一倍。实测教训——
    标记带的标题本来就有截断，副行没有；把文案换成英文后，副行
    ``View direction (X, Y, Z) = (...)`` 比中文长近一倍，直接压到右上角的坐标轴指示器上
    （图上 ``-0.71)`` 与 Z 轴标签叠在一起）。按字符数截断救不了这种情况。

    why 保留 ``keep`` 个字符：全截光会让读者不知道这里本来有内容。
    """
    if draw.textlength(text, font=font) <= max_w:
        return text
    while len(text) > keep and draw.textlength(text + "…", font=font) > max_w:
        text = text[:-1]
    return text + "…"


def add_direction_banner(img_path, title, view_dir, axis_dirs, output_path=None):
    """在图片顶部加一条方向标记带：视角名 + 视线方向向量 + 三轴指示器。

    why：三张图（旧版特写/新版特写/整体定位）现在不再同视角——新旧特写共用一个相机，
    整体图用自动选出的最优方向。若不标注，读者无法判断"为什么同一处差异在两张图里朝向不同"。

    【不标注"前/后/左/右/俯视"这类人类方位词】STP 文件里哪个轴朝上、哪面是正面，
    取决于建模者当时的坐标系约定，本项目无从得知。标世界坐标轴方向 + 箭头指示器是客观事实；
    编个"正视图"出来是臆测，会误导读者。

    【字号按图宽等比】见 BANNER_*_RATIO 的说明：写死像素会在 PPT 里缩成 4~6pt 看不清。
    """
    import math
    from PIL import Image, ImageDraw
    src = Image.open(img_path).convert("RGB")
    W = src.width
    bh = banner_height(W)
    pad = int(W * BANNER_PAD_RATIO)
    f_title = load_font(int(W * BANNER_TITLE_RATIO))
    f_sub = load_font(int(W * BANNER_SUB_RATIO))
    f_ax = load_font(int(W * BANNER_AXIS_RATIO))
    gizmo_r = int(W * AXIS_GIZMO_RATIO)

    out = Image.new("RGB", (W, src.height + bh), BANNER_BG)
    out.paste(src, (0, bh))
    d = ImageDraw.Draw(out)
    # 底部分隔线 + 标题左侧竖色条（让标题在缩小后仍有视觉锚点）
    d.rectangle([0, bh - 3, W, bh - 1], fill=BANNER_LINE)
    bar_w = max(4, int(W * 0.005))
    d.rectangle([pad, pad, pad + bar_w, pad + int(W * BANNER_TITLE_RATIO)],
                fill=BANNER_ACCENT)

    tx = pad + bar_w + int(W * 0.012)
    # 标题与副行的右边界都必须留在坐标轴指示器左侧，否则长文本会把轴标签挤在一起（已实测）。
    # 超长时截断加省略号——标记带是辅助信息，宁可截断也不能破坏版面。
    gizmo_left = W - gizmo_r * 2 - pad - int(W * 0.03)
    max_text_w = gizmo_left - tx
    title = _fit_text(d, title, f_title, max_text_w)
    d.text((tx, pad - int(W * 0.002)), title, font=f_title, fill=BANNER_FG)
    vx, vy, vz = view_dir
    subtitle = _fit_text(d, t("banner.view_dir", x=vx, y=vy, z=vz), f_sub, max_text_w)
    d.text((tx, pad + int(W * BANNER_TITLE_RATIO) + int(W * 0.006)),
           subtitle,
           font=f_sub, fill=BANNER_SUB_FG)

    # 坐标轴指示器：画在标记带右侧
    cx = W - gizmo_r - pad - int(W * 0.02)
    cy = bh // 2
    lw = max(3, int(W * 0.0035))
    for name in ("X", "Y", "Z"):
        dx, dy = axis_dirs.get(name, (0.0, 0.0))
        if abs(dx) < 1e-6 and abs(dy) < 1e-6:
            # 该轴与视线平行，投影成一个点：画成小圆圈表示"指向屏幕内外"
            rr = max(5, int(W * 0.006))
            d.ellipse([cx - rr, cy - rr, cx + rr, cy + rr],
                      outline=AXIS_COLORS[name], width=lw)
            d.text((cx + rr + 4, cy - rr), name, font=f_ax, fill=AXIS_COLORS[name])
            continue
        ex, ey = cx + dx * gizmo_r, cy + dy * gizmo_r
        d.line([cx, cy, ex, ey], fill=AXIS_COLORS[name], width=lw)
        ang = math.atan2(ey - cy, ex - cx)
        head = max(8, int(W * 0.009))
        for sign in (-1, 1):
            a = ang + sign * 2.6
            d.line([ex, ey, ex + head * math.cos(a), ey + head * math.sin(a)],
                   fill=AXIS_COLORS[name], width=lw)
        lab_off = int(W * 0.006)
        d.text((ex + (lab_off if dx >= 0 else -lab_off * 3),
                ey - lab_off * 2), name, font=f_ax, fill=AXIS_COLORS[name])

    out.save(output_path or img_path)
    return bh


def get_bbox_dict(bbox):
    """把 FreeCAD BoundBox 转成 project_bbox 能吃的字典（只需 6 个边界键）。
    结构与 geom_diff.get_bbox_dict 一致，但不 import 那个模块——它是 console 模式脚本。"""
    return {
        "x_min": bbox.XMin, "y_min": bbox.YMin, "z_min": bbox.ZMin,
        "x_max": bbox.XMax, "y_max": bbox.YMax, "z_max": bbox.ZMax,
        "center": [bbox.Center.x, bbox.Center.y, bbox.Center.z],
    }


def split_clusters(shape):
    """把一个差异 Shape 拆成空间分离的簇，按体积降序返回 [{"volume","bbox","size_mm"}, ...]。

    why：对称差几何体常由多块互不相连的材料构成，只取整体 BoundBox 会退化成
    "一个大框套住整个零件"。实测 PART-A 的增料是两块，中心相距 18.3mm，
    而零件本身仅约 19mm 宽，并集 bbox 因此等于整件；撑大它的那块只占差异体积 16%。

    【必须在预算阶段调用】.Solids 是几何读取，受坑 A1 约束（App::Part 的 .Shape 会被
    可见性写操作永久摘除），不得挪到渲染循环里。

    返回值还带 dropped 计数由调用方打印——布尔运算会产生体积近零的碎片，
    静默过滤会让"框覆盖完整"变成假象。
    """
    if shape is None or shape.isNull() or shape.Volume <= 0:
        return [], 0
    try:
        solids = list(shape.Solids)
    except Exception:
        solids = []
    if not solids:
        # 理论上不该发生（Volume>0 却没有 Solid）；退化为整体一个簇，宁可粗也不丢
        bb = shape.BoundBox
        return [{"volume": shape.Volume, "bbox": get_bbox_dict(bb),
                 "size_mm": [bb.XLength, bb.YLength, bb.ZLength]}], 0
    kept = []
    dropped = 0
    for s in solids:
        if s.Volume < CLUSTER_MIN_VOLUME:
            dropped += 1
            continue
        bb = s.BoundBox
        kept.append({"volume": s.Volume, "bbox": get_bbox_dict(bb),
                     "size_mm": [bb.XLength, bb.YLength, bb.ZLength]})
    kept.sort(key=lambda c: -c["volume"])
    return kept, dropped


def project_clusters(av, clusters, flip_y_height):
    """逐簇投影成 2D 矩形，返回 [(rect_native, [簇号]), ...]。只读相机，不动任何状态。

    必须在 capture_and_project 之后紧接着调用，中间不得插入 fitAll/boxZoom/updateGui
    （坑 B2：视口尺寸会在运行中变化，截图与投影必须同源）。
    """
    out = []
    for c in clusters:
        rect = project_bbox(av, c["bbox"], flip_y_height=flip_y_height)
        out.append((rect, [c["index"]]))
    return out


def merge_rects(items, img_w, img_h):
    """把（已含留白的）2D 矩形里互相重叠的合并掉，标签取并集。返回按面积降序的列表。

    why 要合并、且必须每张图各算一次：3D 上分离的簇在某个视角下投影可能重叠
    （实测 PART-C 两簇中心仅相距约 2mm，特写图上必然重叠），
    两个互相穿插的框比一个框更难看。而重叠与否取决于相机，整体图与特写图相机不同，
    所以不能算一次到处复用。
    """
    boxes = []
    dropped = []
    for rect, labels in items:
        x1, y1, x2, y2 = rect
        # Clip BOTH ends to the image, then drop what is left with no area. Clamping
        # each end independently (`max(0, x1)` / `min(img_w, x2)`) is NOT clipping: a
        # rect that sits entirely off-image keeps its far coordinate and ends up
        # inverted (measured: (5000,100,1809,200) for a 1809px-wide image), which
        # PIL rejects with "y1 must be greater than or equal to y0" and the whole
        # render step dies (exit code 2, no manifest, after the geometry work).
        cx1, cy1 = max(0.0, min(float(x1), float(img_w))), max(0.0, min(float(y1), float(img_h)))
        cx2, cy2 = max(0.0, min(float(x2), float(img_w))), max(0.0, min(float(y2), float(img_h)))
        if cx2 <= cx1 or cy2 <= cy1:
            dropped.append((x1, y1, x2, y2, sorted(labels)))
            continue
        boxes.append([cx1, cy1, cx2, cy2, set(labels)])
    if dropped:
        # 禁止无声丢弃：框没了必须让人知道，否则报告会显得「该处差异不存在」
        print(f"    !! {len(dropped)} diff rect(s) lie entirely outside the rendered view "
              f"and were dropped: "
              f"{[(int(r[0]), int(r[1]), int(r[2]), int(r[3])) for r in dropped]}")
    changed = True
    while changed:
        changed = False
        for i in range(len(boxes)):
            for j in range(i + 1, len(boxes)):
                a, b = boxes[i], boxes[j]
                if a[0] <= b[2] and b[0] <= a[2] and a[1] <= b[3] and b[1] <= a[3]:
                    boxes[i] = [min(a[0], b[0]), min(a[1], b[1]),
                                max(a[2], b[2]), max(a[3], b[3]), a[4] | b[4]]
                    boxes.pop(j)
                    changed = True
                    break
            if changed:
                break
    result = [((b[0], b[1], b[2], b[3]), sorted(b[4])) for b in boxes]
    result.sort(key=lambda r: -((r[0][2] - r[0][0]) * (r[0][3] - r[0][1])))
    if len(result) > MAX_RECTS:
        # 禁止无声截断：被丢掉的框必须让人看得见，否则"全都框住了"就成了假象
        print(f"    !! Rect count {len(result)} exceeds the limit of {MAX_RECTS}; "
              f"keeping the top {MAX_RECTS} by area, dropping cluster ids "
              f"{sorted(l for _, ls in result[MAX_RECTS:] for l in ls)}")
        result = result[:MAX_RECTS]
    return result


def _region_emptiness(arr, x1, y1, x2, y2):
    """区域"空白度"：非白像素占比的反数（1.0=纯白）。用于给标签找真正空的落点。

    why 要实测而不是固定放某个角：框外哪一侧是空白完全取决于该视角下模型的形状与位置，
    固定放"右上角"在某些图上正好压在零件或另一个框上。
    """
    h, w = arr.shape[:2]
    x1, y1 = max(0, int(x1)), max(0, int(y1))
    x2, y2 = min(w, int(x2)), min(h, int(y2))
    if x2 <= x1 or y2 <= y1:
        return -1.0            # 越界：不可用
    sub = arr[y1:y2, x1:x2]
    nonwhite = (sub.sum(axis=2) < 730).sum()
    return 1.0 - nonwhite / float((x2 - x1) * (y2 - y1))


def _leader_cost(arr, x0, y0, x1, y1):
    """引出线路径上非白像素的占比（0=全程走空白，1=全程压在内容上）。

    why 必须评估路径而不只看落点：只按"落点是否空白"选位时，实测会选出图片边缘的
    空白角落，引出线因此长距离横穿零件本体，比标签贴在框边更难看。
    """
    n = 24
    hit = 0
    h, w = arr.shape[:2]
    for i in range(1, n + 1):
        t = i / float(n + 1)
        x = int(x0 + (x1 - x0) * t)
        y = int(y0 + (y1 - y0) * t)
        if 0 <= x < w and 0 <= y < h and arr[y, x].sum() < 730:
            hit += 1
    return hit / float(n)


def _place_label(arr, rect, lw, lh, gap, occupied, img_w, img_h):
    """给一个框选标签落点：在框外八个方向的候选位里，挑既不越界、又最空、引出线也不
    横穿内容的那个。返回 (lx, ly, 锚点在框上的连线起点)。

    why 放在框【外】：标签画在框内时，框小的时候标签几乎填满整个框，把差异本身盖住
    （用户反馈）。改为框外 + 引出线，框内只留纯净的差异画面。

    评分 = 落点空白度 − 引出线穿越代价。两项都必须算：只看落点会选中图片边缘的空角、
    让引出线长距离横穿零件（实测），只看路径又会把标签压在内容上。
    """
    x1, y1, x2, y2 = rect
    cx, cy = (x1 + x2) / 2, (y1 + y2) / 2
    # 候选：(标签左上, 框上连线锚点)。顺序即同分时的偏好——先上下、再左右、最后斜角。
    cands = [
        ((cx - lw / 2, y1 - gap - lh), (cx, y1)),                 # 正上
        ((cx - lw / 2, y2 + gap), (cx, y2)),                      # 正下
        ((x1 - gap - lw, cy - lh / 2), (x1, cy)),                 # 正左
        ((x2 + gap, cy - lh / 2), (x2, cy)),                       # 正右
        ((x1 - gap - lw, y1 - gap - lh), (x1, y1)),               # 左上
        ((x2 + gap, y1 - gap - lh), (x2, y1)),                    # 右上
        ((x1 - gap - lw, y2 + gap), (x1, y2)),                    # 左下
        ((x2 + gap, y2 + gap), (x2, y2)),                         # 右下
    ]
    best = None
    for (lx, ly), anchor in cands:
        if lx < 0 or ly < 0 or lx + lw > img_w or ly + lh > img_h:
            continue
        # 与已放置的标签重叠则跳过（多框时标签不能叠在一起）
        if any(lx < o[2] and o[0] < lx + lw and ly < o[3] and o[1] < ly + lh
               for o in occupied):
            continue
        empty = _region_emptiness(arr, lx, ly, lx + lw, ly + lh)
        if empty < 0:
            continue
        cost = _leader_cost(arr, lx + lw / 2, ly + lh / 2, anchor[0], anchor[1])
        score = empty - cost
        if best is None or score > best[0]:
            best = (score, lx, ly, anchor)
    if best is None:
        # 八个方向全不可用（框贴边且四周被占）：退回框内左上角，至少不丢信息
        return x1 + lw * 0.1, y1 + lh * 0.1, (x1, y1)
    return best[1], best[2], best[3]


def draw_rects_on_image(img_path, rect_items_native, output_path, with_labels=True):
    """在图上画若干红框（每处差异各一个），返回实际画出的 [(rect_upscaled, labels), ...]。

    rect_items_native 是原生视口坐标系下的 [(rect, labels)]；img_path 已是放大
    UPSCALE_FACTOR 倍后的图片，所以画框前要把坐标同比例放大，否则框会缩在左上角一小块。

    描边向【外】扩展（x1-offset），所以加粗只占外侧白边，不会盖住差异几何体本身。

    【编号必须标在框外，用引出线连回框】曾把红底白字标签画在框左上角【内侧】，
    框小时标签几乎填满整个框、把差异本身盖住（用户反馈）。现在改为：标签放框外
    最空白的一侧（逐图实测非白像素占比选位，见 _place_label），一根细线连回框边锚点。
    落点是实测选出来的而非固定方位——框外哪一侧空白完全取决于该视角下模型的位置。
    """
    import numpy as np
    from PIL import Image, ImageDraw
    img = Image.open(img_path).convert("RGB")
    draw = ImageDraw.Draw(img)
    arr = np.asarray(img).astype(np.int16)

    scaled = []
    for rect, labels in rect_items_native:
        x1, y1, x2, y2 = [v * UPSCALE_FACTOR for v in rect]
        scaled.append(((x1 - PADDING_PX, y1 - PADDING_PX,
                        x2 + PADDING_PX, y2 + PADDING_PX), labels))
    merged = merge_rects(scaled, img.width, img.height)

    line_w = max(RECT_MIN_WIDTH, int(img.width * RECT_WIDTH_RATIO))
    f_label = load_font(int(img.width * RECT_LABEL_RATIO))
    multi = len(merged) > 1
    for (x1, y1, x2, y2), labels in merged:
        for offset in range(line_w):
            draw.rectangle([x1 - offset, y1 - offset, x2 + offset, y2 + offset],
                           outline=RECT_COLOR)

    # 标签单独一轮画：必须等全部框描完，否则先画的标签会把后画的框当"非白内容"，
    # 反之先画的框也影响不到已选好的落点——两轮分开才能让空白度评估看到完整的框。
    occupied = []
    if with_labels and multi:
        for (x1, y1, x2, y2), labels in merged:
            if not labels:
                continue
            text = "".join(CIRCLED_DIGITS[i - 1] if 1 <= i <= len(CIRCLED_DIGITS)
                           else str(i) for i in labels)
            tw = draw.textlength(text, font=f_label)
            th = int(img.width * RECT_LABEL_RATIO * 1.25)
            pad = max(2, int(img.width * 0.004))
            lw_box, lh_box = tw + pad * 2, th + pad * 2
            gap = int(img.width * RECT_LABEL_GAP_RATIO)
            lx, ly, anchor = _place_label(arr, (x1, y1, x2, y2), lw_box, lh_box,
                                          gap, occupied, img.width, img.height)
            # 引出线：从标签框中心连到框边锚点。细线（描边的一半宽）以免抢视觉。
            leader_w = max(1, line_w // 2)
            draw.line([lx + lw_box / 2, ly + lh_box / 2, anchor[0], anchor[1]],
                      fill=RECT_COLOR, width=leader_w)
            # 标签本体：白底 + 红框 + 红字。白底盖住引出线末端，读数不被线穿过；
            # 红字比红底白字更轻，落在图外空白处不会形成抢眼的色块。
            draw.rectangle([lx, ly, lx + lw_box, ly + lh_box],
                           fill=(255, 255, 255), outline=RECT_COLOR,
                           width=max(1, leader_w))
            draw.text((lx + pad, ly + pad), text, font=f_label, fill=RECT_COLOR)
            occupied.append((lx, ly, lx + lw_box, ly + lh_box))

    img.save(output_path)
    return merged, line_w


def hide_all_objects(md, doc):
    for obj in doc.Objects:
        try:
            vobj = md.getObject(obj.Name)
            if vobj:
                vobj.Visibility = False
        except Exception:
            pass


def show_object_and_children(md, obj):
    try:
        vobj = md.getObject(obj.Name)
        if vobj:
            vobj.Visibility = True
    except Exception:
        pass
    if hasattr(obj, 'Group'):
        for child in obj.Group:
            show_object_and_children(md, child)


def show_with_ancestors(md, obj):
    """FreeCAD子对象要渲染出来，其所有父级App::Part装配体容器也必须Visibility=True，
    否则hide_all_objects隐藏的父级会导致子对象即使自身可见也渲染不出来（空白图）。"""
    node = obj
    seen = set()
    while node is not None and node.Name not in seen:
        seen.add(node.Name)
        try:
            vobj = md.getObject(node.Name)
            if vobj:
                vobj.Visibility = True
        except Exception:
            pass
        parents = node.InList
        node = parents[0] if parents else None


def set_transparency_recursive(md, obj, value):
    """差异高亮体常贴在零件内表面或边缘（对称差几何天然如此），若零件本身完全不透明，
    从某些视角看正好被零件挡住。只对真正的几何ViewProvider设置
    （App::Part容器本身的ViewProvider没有Transparency属性，需递归下探到几何子对象）。"""
    try:
        vobj = md.getObject(obj.Name)
        if vobj and hasattr(vobj, "Transparency"):
            vobj.Transparency = value
    except Exception:
        pass
    if hasattr(obj, "Group"):
        for child in obj.Group:
            set_transparency_recursive(md, child, value)


def collect_subtree_names(obj, acc):
    """收集obj及其全部后代的Name。用于区分"目标零件子树"与"周围其他零件"。"""
    acc.add(obj.Name)
    if hasattr(obj, "Group"):
        for child in obj.Group:
            collect_subtree_names(child, acc)


def setup_overview_state(md, doc, injected_names, self_names, highlight_feat):
    """建立整体定位图的渲染状态：显式覆写【每一个】真实几何的可见性与透明度。

    why 必须显式覆写全部对象，而不是只改目标零件：
    1. 非目标零件不透明会把差异高亮体挡死。实测 PART-C 在全部 26 个方向上
       都是 0 个品红，把周围零件设为 CONTEXT_TRANSPARENCY=85 之后才透出 267px
       （这就是"整体图上看不到差异标记"的直接原因）。
    2. 上一轮渲染留下的透明度会污染本轮取景。每轮从零覆写全部对象是保证可复现的唯一办法，
       不要改成"只改本轮涉及的对象"——调查期间正因如此出现过同一状态两次测得不同结果。

    排除 injected_names：注入的高亮体也在 doc.Objects 里，无差别点亮会让前几轮的高亮体
    在本轮整体图上重现，使品红指向错误的零件（坑 C1）。
    """
    for obj in doc.Objects:
        if not is_real_geometry(obj) or obj.Name in injected_names:
            continue
        try:
            vobj = md.getObject(obj.Name)
            if vobj is None:
                continue
            vobj.Visibility = True
            if hasattr(vobj, "Transparency"):
                vobj.Transparency = (TARGET_TRANSPARENCY if obj.Name in self_names
                                     else CONTEXT_TRANSPARENCY)
        except Exception:
            pass
    for name in injected_names:
        try:
            vobj = md.getObject(name)
            if vobj:
                vobj.Visibility = False
        except Exception:
            pass
    if highlight_feat is not None:
        set_highlight_style(md, highlight_feat)


def pick_best_direction(av, bbox, tmp_path):
    """扫描 VIEW_DIRECTIONS 全部候选方向，返回 (方向名, 方向向量, 最优明细, 全部候选明细)。

    这是"整体图暴露差异点"的核心：不再固定 viewIsometric()（那个视角与特写图同向，
    差异点常被自身或兄弟零件挡住），而是逐差异实测每个方向下差异高亮的可见性与画面可读性，
    取综合分最高者。评分依据见 score_direction。

    调用方必须先建立好渲染状态（setup_overview_state）；本函数只动相机，不动可见性。
    每个候选方向都要重新投影 bbox——相机变了红框位置也随之变。

    【两级选择，防止选出"有品红但看不懂"的糊团图】
    先在"品红真的看得见（sat_in >= SCORE_MIN_VISIBLE_PX）且画面可读（readability >=
    SCORE_READABILITY_FLOOR）"的候选里挑综合分最高者；这一档全空时（说明画面清楚的方向
    都看不到品红），退而在全部候选里挑可读性最高者，此时整体图纯靠红框定位，
    并置 fallback 标记供调用方告警、供 PPT 向读者交代原因。

    why 不直接取综合分最高：综合分是可见分×可读性的乘积，若只有一个方向能看到品红，
    它无论多糊都会胜出——实测 PART-C 就选出了实体占比仅 0.028 的线框糊团图。
    why 可见性门槛不能写"非零"：同一差异在 -1+1+0 方向恰好有 1 个品红像素，
    数值上非零但肉眼看不见，会挤掉正确的 fallback 判定。
    """
    rows = []
    for tag, vec in VIEW_DIRECTIONS:
        n_zero = sum(1 for c in vec if abs(c) < 1e-9)
        av.setViewDirection(vec)
        FreeCADGui.updateGui()
        av.fitAll()
        FreeCADGui.updateGui()
        time.sleep(0.12)
        size = av.getSize()
        av.saveImage(tmp_path, size[0], size[1], 'White')
        rect = project_bbox(av, bbox, flip_y_height=size[1])
        score, detail = score_direction(tmp_path, rect, n_zero)
        detail.update({"dir": tag, "vec": vec, "score": round(score, 4)})
        rows.append(detail)

    eligible = [r for r in rows
                if r["readability"] >= SCORE_READABILITY_FLOOR
                and r["sat_in"] >= SCORE_MIN_VISIBLE_PX]
    if eligible:
        best = max(eligible, key=lambda r: r["score"])
        best["fallback"] = False
    else:
        # 没有任何"看得懂"的方向能看见品红：选画面最清楚的，靠红框定位
        best = max(rows, key=lambda r: r["readability"])
        best["fallback"] = True
    rows.sort(key=lambda r: -r["score"])
    return best["dir"], best["vec"], best, rows


def get_shape(obj):
    """取对象的Shape。Part::Feature和App::Part都能直接访问.Shape
    （App::Part返回其Group的聚合Shape，已含容器Placement变换）。

    【务必在任何Visibility写操作之前调用】App::Part的.Shape属性会在可见性被触及后永久消失。
    不要试图改成"容器取不到就下探融合Group子对象Shape"来兜底——已实测：子对象的原始Shape
    处于容器局部坐标系，且用 容器Placement×子Placement 复原出的bbox与容器Shape的bbox不一致，
    据此算出的对称差是 341+346=687mm³（两形状零重叠）的垃圾数据，而非真实的 4.87mm³。
    """
    try:
        shp = obj.Shape
        if shp is None or shp.isNull():
            return None
        return shp
    except Exception:
        return None


def add_feature(doc, shape, name, color=None):
    """把一个Shape作为新Part::Feature加入文档。color为None时保持默认灰色。"""
    feat = doc.addObject("Part::Feature", name)
    feat.Shape = shape
    return feat


def precompute_shapes(actual_diffs, label_map, old_label_map):
    """【阶段1/3：预算】在任何可见性操作之前，一次性算出全部差异几何体。

    这个阶段必须整体早于任何 Visibility/Transparency 写操作 —— App::Part 容器的 .Shape
    属性会被可见性操作永久摘除（详见模块开头说明与 get_shape 注释）。

    对称差拆成 removed(旧有新无=被去掉的材料) 和 added(新有旧无=新增的材料) 两半，
    分别用于旧版/新版特写图的高亮。这不额外增加布尔运算成本——完整对称差
    old.cut(new).fuse(new.cut(old)) 本来就要算这两个中间结果。
    """
    results = {}
    for d_idx, diff_entry in enumerate(actual_diffs):
        bn = diff_entry["base_name"]
        for c_idx, change in enumerate(diff_entry["geometric_changes"]):
            key = (d_idx, c_idx)
            old_label = change["old_label"]
            new_label = change.get("new_label", "")
            old_obj = old_label_map.get(old_label)
            new_obj = label_map.get(new_label)

            entry = {"old_shape": None, "new_shape": None,
                     "removed": None, "added": None, "diff_shape": None,
                     "clusters": [], "new_obj": new_obj,
                     "change_type": change.get("change_type", "shape_changed")}

            if old_obj is None or new_obj is None:
                print(f"  !! {bn}: object not found old={old_label}({old_obj is not None}) "
                      f"new={new_label}({new_obj is not None}); skipping highlight")
                results[key] = entry
                continue

            old_shape = get_shape(old_obj)
            new_shape = get_shape(new_obj)
            if old_shape is None or new_shape is None:
                print(f"  !! {bn}: cannot get Shape old={old_shape is not None} "
                      f"new={new_shape is not None}; skipping highlight")
                results[key] = entry
                continue

            if entry["change_type"] == "moved":
                # 位移类不做布尔运算：两形状零重叠时对称差等于"整件被去掉+整件新增"，
                # 既昂贵又会把读者误导成零件被换掉了（geom_diff 已按此不算）。
                # 这里只把两版本体留着，渲染时分别整体高亮。
                entry.update({"old_shape": old_shape, "new_shape": new_shape})
                print(f"  {bn}: moved diff, skipping boolean ops "
                      f"(translation {change.get('translation_mm')}mm / "
                      f"rotation {change.get('rotation_deg')}°)")
                results[key] = entry
                continue

            if change.get("highlight_mode") == "whole_part":
                # 降级路径：geom_diff 那边布尔算不出对称差（OCCT 对近乎重合的形状返回
                # 退化结果，见坑 J2），但体积差已证明零件被改过。这里把两版零件【本体】
                # 当作 removed/added——语义成立：旧版整件在新版不再原样存在、新版整件是
                # "新的形状"，于是高亮区域是整个零件。比精确的增减料范围粗，但差异不丢、
                # 也不静默（degraded_reason 落进渲染记录与报告）。
                rem_cl, rem_drop = split_clusters(old_shape)
                add_cl, add_drop = split_clusters(new_shape)
                clusters = ([dict(c, role="removed") for c in rem_cl]
                            + [dict(c, role="added") for c in add_cl])
                clusters.sort(key=lambda c: -c["volume"])
                for i, c in enumerate(clusters, 1):
                    c["index"] = i
                entry.update({"old_shape": old_shape, "new_shape": new_shape,
                              "removed": old_shape, "added": new_shape,
                              "diff_shape": new_shape, "clusters": clusters,
                              "degraded_reason": change.get("degraded_reason")})
                print(f"  {bn}: DEGRADED highlight (whole part) — "
                      f"{change.get('degraded_reason')}; "
                      f"V_old={old_shape.Volume:.4f} V_new={new_shape.Volume:.4f}")
                results[key] = entry
                continue

            t0 = time.time()

            removed = old_shape.cut(new_shape)
            added = new_shape.cut(old_shape)
            diff_shape = removed.fuse(added)
            # 分簇必须在这里做：.Solids 是几何读取，受坑 A1 约束，不能挪到渲染循环里。
            # 编号跨 removed/added 统一按体积降序排（1..N），与 PPT 左栏的尺寸清单同序。
            rem_cl, rem_drop = split_clusters(removed)
            add_cl, add_drop = split_clusters(added)
            clusters = ([dict(c, role="removed") for c in rem_cl]
                        + [dict(c, role="added") for c in add_cl])
            clusters.sort(key=lambda c: -c["volume"])
            for i, c in enumerate(clusters, 1):
                c["index"] = i
            entry.update({"old_shape": old_shape, "new_shape": new_shape,
                          "removed": removed, "added": added, "diff_shape": diff_shape,
                          "clusters": clusters})
            print(f"  {bn}: symmetric difference V={diff_shape.Volume:.6f}mm3 "
                  f"(removed {removed.Volume:.4f} / added {added.Volume:.4f}) "
                  f"{time.time()-t0:.1f}s")
            print(f"    {len(clusters)} clusters (removed {len(rem_cl)} / added {len(add_cl)}), "
                  f"{rem_drop + add_drop} fragments filtered: "
                  + ", ".join(f"#{c['index']}{c['role'][:3]} V={c['volume']:.4f}"
                              for c in clusters))
            results[key] = entry
    return results


def inject_features(doc, actual_diffs, precomputed):
    """【阶段2/3：注入】把预算出的几何体作为Part::Feature写进渲染文档。

    注入的对象必须登记进 injected_names 并在整体图渲染时排除 —— 否则"显示所有真实几何"的
    循环会把前几轮注入的高亮体一起点亮。已实测该 bug 曾导致 PART-D 与 PART-A 两张
    整体图的品红像素数和坐标完全相同（89个、同一位置），即后者显示的是前者的残留，指错零件。
    """
    injected = {}
    injected_names = set()
    for d_idx, diff_entry in enumerate(actual_diffs):
        bn = diff_entry["base_name"]
        safe_bn = bn.replace("-", "_")
        for c_idx, _ in enumerate(diff_entry["geometric_changes"]):
            key = (d_idx, c_idx)
            pre = precomputed.get(key, {})
            feats = {}

            if pre.get("change_type") == "moved":
                # 位移类：注入两版【零件本体】各一份作为高亮体。品红在这里代表
                # "整体发生位移的零件"，语义由标记带与 PPT 文字带说明（不靠读者猜色标）。
                # 两者都必须登记进 injected_names（坑 C1：否则整体图会把它们无差别点亮）。
                for role, shape_key in (("moved_old", "old_shape"),
                                        ("moved_new", "new_shape")):
                    shp = pre.get(shape_key)
                    if shp is None or shp.isNull() or shp.Volume <= 0:
                        feats[role] = None
                        continue
                    prefix = {"moved_old": "MovedOldHL", "moved_new": "MovedNewHL"}[role]
                    feat = add_feature(doc, shp, f"{prefix}_{safe_bn}_{d_idx}")
                    feats[role] = feat
                    injected_names.add(feat.Name)
                feats["old_proxy"] = None
                injected[key] = feats
                continue

            # removed/added 可能为空Shape（纯增料或纯减料），空则不注入，
            # 对应图上就没有品红——这是正确结果，不是失败。
            for role, shape_key in (("diff", "diff_shape"), ("added", "added"), ("removed", "removed")):

                shp = pre.get(shape_key)
                if shp is None or shp.isNull() or shp.Volume <= 0:
                    feats[role] = None
                    continue
                prefix = {"diff": "DiffHL", "added": "AddedHL", "removed": "RemovedHL"}[role]
                feat = add_feature(doc, shp, f"{prefix}_{safe_bn}_{d_idx}")
                feats[role] = feat
                injected_names.add(feat.Name)
            # 旧版零件本体的代理对象：Shape是值对象，跨文档赋值即拷贝几何，世界坐标不变，
            # 因此它与新版零件天然处于同一世界坐标系，同相机渲染即可像素级对齐。
            old_shape = pre.get("old_shape")
            if old_shape is not None and not old_shape.isNull():
                proxy = add_feature(doc, old_shape, f"OldProxy_{safe_bn}_{d_idx}")
                feats["old_proxy"] = proxy
                injected_names.add(proxy.Name)
            else:
                feats["old_proxy"] = None
            injected[key] = feats

    doc.recompute()
    return injected, injected_names


def set_highlight_style(md, feat):
    """把注入的高亮体设为品红不透明。"""
    vobj = md.getObject(feat.Name)
    vobj.Visibility = True
    vobj.ShapeColor = HIGHLIGHT_COLOR
    vobj.Transparency = 0


def hide_injected(md, injected_names):
    for name in injected_names:
        try:
            vobj = md.getObject(name)
            if vobj:
                vobj.Visibility = False
        except Exception:
            pass


def render_one(md, av, doc, output_dir, bn, idx, change, bbox,
               new_obj, feats, injected_names, pre, self_names,
               label_old=None, label_new=None):
    """渲染单个差异的三张图：整体定位图 + 新版特写图 + 旧版特写图。
    返回该差异的 manifest 记录。

    label_old/label_new 是版本标签，由调用方按 STP 文件名或 CLI 参数推导——
    早期这里写死了 "V4" / "V4-UPDATE"，换任何一对 STP 图上都还标着 V4。

    【红框是逐差异簇分别画的，不是一个并集大框】见 split_clusters 的说明。
    但 boxZoom 取景与 score_direction 评分仍用并集 bbox：取景换成单簇会让新旧特写
    覆盖区域不一致、破坏坑 C2 的像素级对齐，评分换成多框则会改变整体图选向结果。
    """
    # 默认标签在函数体内取：签名默认值在 import 时就求值，那时 --lang 还没解析（见 i18n）
    label_old = label_old or t("label.old")
    label_new = label_new or t("label.new")
    rec = {"base_name": bn, "instance_index": change["instance_index"],
           "change_type": change.get("change_type", "shape_changed"),
           "label_old": label_old, "label_new": label_new}
    # 降级标记（坑 J2）：布尔算不出对称差时高亮的是整个零件而非精确增减料范围，
    # 必须一路传到 manifest 与报告——读者有权知道这处高亮是粗的。
    if change.get("degraded_reason"):
        rec["degraded_reason"] = change["degraded_reason"]

    clusters = pre.get("clusters") or []
    rec["cluster_count"] = len(clusters)
    rec["cluster_details"] = [
        {"index": c["index"], "role": c["role"],
         "volume": c["volume"], "size_mm": c["size_mm"]}
        for c in clusters
    ]

    # === (a) 整体定位图 ===
    # 【与旧版的关键差别】不再固定 viewIsometric()。整体图的职责是"让读者看清差异点
    # 位于装配体的哪个部位"，与特写图同视角时差异标记多半被自身或兄弟零件挡住，
    # 等于把这张图的信息量归零。改为：把非目标零件设高透明 + 扫描26个候选方向，
    # 按"品红可见性 x 画面可读性"的综合分自动选出最能暴露差异点的方向。
    print("  Rendering overview image (scanning candidate view directions)...")
    setup_overview_state(md, doc, injected_names, self_names, feats.get("diff"))
    FreeCADGui.updateGui()

    scan_tmp = os.path.join(output_dir, f".scan_{bn}_{idx}.png")
    dir_tag, dir_vec, dir_detail, dir_rows = pick_best_direction(av, bbox, scan_tmp)
    try:
        os.remove(scan_tmp)
    except OSError:
        pass
    runner_up = dir_rows[1]["dir"] if len(dir_rows) > 1 else "—"
    print(f"    Selected view {dir_tag} (score={dir_detail['score']:.4f} "
          f"magenta_in_box={dir_detail['sat_in']} components={dir_detail['n_cc']} "
          f"solidity={dir_detail['solidity']} body_pct={dir_detail['body_pct']} "
          f"readability={dir_detail['readability']}), runner-up {runner_up}")
    if dir_detail.get("fallback"):
        print(f"    !! {bn}: no readable direction shows the diff highlight (this diff is "
              f"buried inside the assembly, blocked by other parts). Fell back to the "
              f"clearest direction: the overview relies on red rects for location, "
              f"see the close-ups for diff detail.")
    elif dir_detail["sat_in"] == 0:
        print(f"    !! WARNING: {bn} shows no diff highlight in any of the "
              f"{len(VIEW_DIRECTIONS)} candidate directions")

    # 定回最优方向（扫描结束时相机停在最后一个候选上，必须重新设定）
    av.setViewDirection(dir_vec)
    FreeCADGui.updateGui()
    av.fitAll()
    FreeCADGui.updateGui()
    time.sleep(1)

    overview_path = os.path.join(output_dir, f"{bn}_{idx}_overview.png")
    ov_size, bbox_2d = capture_and_project(av, overview_path, bbox)
    # 逐簇投影必须紧跟 capture_and_project、中间不插入 fitAll/boxZoom/updateGui
    # （坑 B2：视口尺寸会在运行中变化，截图与投影必须同源）。
    ov_items = project_clusters(av, clusters, ov_size[1]) if clusters else [(bbox_2d, [])]
    overview_axes = axis_screen_dirs(av)
    overview_rect_path = os.path.join(output_dir, f"{bn}_{idx}_overview_rect.png")
    ov_rects, line_w = draw_rects_on_image(overview_path, ov_items, overview_rect_path)
    rec["overview"] = os.path.basename(overview_path)
    rec["overview_rect"] = os.path.basename(overview_rect_path)
    rec["bbox_2d"] = list(bbox_2d)
    # 逐簇框，合并重叠后的最终结果。坐标是【放大后图片的像素坐标、已含 PADDING_PX】，
    # 即与 *_rect.png 上实际画出的框一致，下游可直接拿它与品红像素比对
    # （注意加标记带后 y 要下移 banner_h）。bbox_2d 保留为并集，仍供取景与溢出类断言用。
    rec["rects_overview"] = [{"rect": [round(v, 1) for v in rect], "labels": labels}
                             for rect, labels in ov_rects]
    rec["rect_line_width_px"] = line_w
    rec["magenta_px_overview"] = count_magenta(overview_rect_path)
    rec["overview_view_dir"] = [round(v, 4) for v in dir_vec]
    rec["overview_view_tag"] = dir_tag
    rec["overview_view_score"] = dir_detail["score"]
    rec["overview_sat_in"] = dir_detail["sat_in"]
    rec["overview_sat_out"] = dir_detail["sat_out"]
    # fallback=True 表示"所有画面可读的方向都看不到该差异（藏在装配体内部）"，
    # 此时整体图纯靠红框定位。build_pptx.py 据此在文字带里向读者交代原因。
    rec["overview_view_fallback"] = bool(dir_detail.get("fallback"))
    rec["overview_view_readability"] = dir_detail["readability"]
    # 画面质量分项。fallback 档位下 solidity 必然为 0（没有品红可测），
    # 于是 readability 被压到下限，无法用它判断"画面是否可读"——必须看这两个独立于品红的量。
    rec["overview_ecc"] = dir_detail["ecc"]
    rec["overview_compose"] = dir_detail["compose"]
    rec["overview_body_pct"] = dir_detail["body_pct"]
    rec["overview_axis_dirs"] = {k: [round(c, 4) for c in v]
                                 for k, v in overview_axes.items()}
    print(f"    Overview magenta_px={rec['magenta_px_overview']}  rects={len(ov_rects)}")

    # === (b) 新版特写图 ===
    # 特写图不能只fitAll到整个零件——差异往往只占零件自身几mm的一小块区域
    # （相对几十mm的零件尺寸），fitAll到整个零件后差异在视觉上几乎看不出来。
    # 改为先fitAll看清零件，再boxZoom聚焦到差异bbox本身（留边距）。
    print("  Rendering new-version close-up...")
    hide_all_objects(md, doc)
    if new_obj is not None:
        show_with_ancestors(md, new_obj)
        show_object_and_children(md, new_obj)
        set_transparency_recursive(md, new_obj, TARGET_TRANSPARENCY)
    if feats.get("added") is not None:
        set_highlight_style(md, feats["added"])
    FreeCADGui.updateGui()
    # 特写图沿用等轴视角：它只显示目标零件本身、没有兄弟零件遮挡，等轴能同时看到三个面，
    # 是零件级观察的通用选择；这里必须显式设回，否则会沿用上面整体图扫出来的方向。
    av.viewIsometric()
    FreeCADGui.updateGui()
    av.fitAll()
    FreeCADGui.updateGui()
    time.sleep(1)

    # boxZoom 吃的是"视口"坐标（与 getPointOnScreen 同一套下原点坐标系），
    # 所以这里【故意不做 y 翻转】——翻转只用于把坐标对齐到图像像素，与 boxZoom 无关。
    x1, y1, x2, y2 = project_bbox(av, bbox)
    zoom_pad = max(20, int(0.3 * max(x2 - x1, y2 - y1)))
    # boxZoom 的签名是四个 int 参数，不接受 tuple——已实测确认，别改成传元组。
    av.boxZoom(int(x1 - zoom_pad), int(y1 - zoom_pad), int(x2 + zoom_pad), int(y2 + zoom_pad))
    FreeCADGui.updateGui()
    time.sleep(1)

    closeup_new_path = os.path.join(output_dir, f"{bn}_{idx}_closeup_new.png")
    closeup_size, bbox_2d_closeup = capture_and_project(av, closeup_new_path, bbox)
    # 逐簇投影：紧跟截图、同一份尺寸（坑 B2）。相机与整体图不同，故必须重新投影，
    # 不能复用 ov_items——3D 上分离的簇在不同视角下重叠关系不一样。
    cu_items = (project_clusters(av, clusters, closeup_size[1]) if clusters
                else [(bbox_2d_closeup, [])])
    closeup_axes = axis_screen_dirs(av)
    closeup_dir_vec = tuple(av.getViewDirection())
    if not check_image_nonblank(closeup_new_path):
        print(f"    !! WARNING: new-version close-up is all white; "
              f"check the parent_chain/visibility logic for {bn}")
    closeup_new_rect = os.path.join(output_dir, f"{bn}_{idx}_closeup_new_rect.png")
    cu_rects, _ = draw_rects_on_image(closeup_new_path, cu_items, closeup_new_rect)
    rec["closeup_new"] = os.path.basename(closeup_new_path)
    rec["closeup_new_rect"] = os.path.basename(closeup_new_rect)
    rec["bbox_2d_closeup"] = list(bbox_2d_closeup)
    rec["rects_closeup"] = [{"rect": [round(v, 1) for v in rect], "labels": labels}
                            for rect, labels in cu_rects]
    rec["magenta_px_closeup_new"] = count_magenta(closeup_new_rect)
    print(f"    New close-up magenta_px={rec['magenta_px_closeup_new']}  rects={len(cu_rects)}")
    if feats.get("added") is not None and rec["magenta_px_closeup_new"] == 0:
        print(f"    !! WARNING: {bn} has added-material geometry but the new-version "
              f"close-up has 0 magenta pixels; the highlight was not rendered")

    # === (c) 旧版特写图 ===
    # 【绝对不要在此处动相机】复用(b)结束时的相机状态，这是新旧两图能像素级对齐的唯一保证。
    # 不要调 fitAll / boxZoom / viewIsometric，也不要在(b)与(c)之间插入任何相机操作。
    print("  Rendering old-version close-up (reusing the new-version camera, view unchanged)...")
    hide_all_objects(md, doc)
    if feats.get("old_proxy") is not None:
        vp = md.getObject(feats["old_proxy"].Name)
        vp.Visibility = True
        vp.Transparency = TARGET_TRANSPARENCY
    if feats.get("removed") is not None:
        set_highlight_style(md, feats["removed"])
    FreeCADGui.updateGui()
    time.sleep(1)

    closeup_old_path = os.path.join(output_dir, f"{bn}_{idx}_closeup_old.png")
    # 必须用与新版特写图相同的尺寸截图：两图共用同一相机与同一份投影坐标，
    # 尺寸不同则画面缩放比例不同，红框会对不上，也破坏并排对比的像素级对齐。
    save_native_and_upscale(av, closeup_old_path, closeup_size)
    closeup_old_rect = os.path.join(output_dir, f"{bn}_{idx}_closeup_old_rect.png")
    # 用与新版特写图完全相同的框（同相机 → 坐标必然一致，逐簇结果也一致）。
    # 【不要在这里重新投影】(b) 与 (c) 之间不得有任何相机操作，复用是像素级对齐的保证（坑 C2）。
    draw_rects_on_image(closeup_old_path, cu_items, closeup_old_rect)
    rec["closeup_old"] = os.path.basename(closeup_old_path)
    rec["closeup_old_rect"] = os.path.basename(closeup_old_rect)
    rec["magenta_px_closeup_old"] = count_magenta(closeup_old_rect)
    print(f"    Old close-up magenta_px={rec['magenta_px_closeup_old']}")
    if feats.get("removed") is not None and rec["magenta_px_closeup_old"] == 0:
        print(f"    !! WARNING: {bn} has removed-material geometry but the old-version "
              f"close-up has 0 magenta pixels; the highlight was not rendered")

    # === 数值字段 ===
    rec["old_volume"] = change.get("old_volume")
    rec["new_volume"] = change.get("new_volume")
    rec["volume_delta"] = change.get("volume_delta")
    rec["volume_delta_pct"] = change.get("volume_delta_pct")
    rec["diff_bbox_size_mm"] = change.get("diff_bbox_size_mm")
    removed = pre.get("removed")
    added = pre.get("added")
    rec["removed_volume"] = removed.Volume if removed is not None and not removed.isNull() else 0.0
    rec["added_volume"] = added.Volume if added is not None and not added.isNull() else 0.0

    # === 方向标记带 ===
    # 三张图现在不再同视角（新旧特写共用等轴相机，整体图用自动选出的最优方向），
    # 不标注读者就无法理解"为什么同一处差异在两张图里朝向不同"。
    # 品红像素统计必须在【加带之前】完成（上面已完成）——标记带是白底，会改变图像尺寸，
    # 若在加带后统计，红框坐标与像素坐标就对不上了。
    rec["closeup_view_dir"] = [round(v, 4) for v in closeup_dir_vec]
    rec["closeup_axis_dirs"] = {k: [round(c, 4) for c in v] for k, v in closeup_axes.items()}
    banner_jobs = [
        (rec["overview_rect"], t("banner.title.overview", tag=dir_tag), dir_vec, overview_axes),
        (rec["closeup_new_rect"], t("banner.title.closeup", label=label_new),
         closeup_dir_vec, closeup_axes),
        # 标题要短：字号按图宽等比放大后，长标题会顶到右侧坐标轴指示器上把轴标签挤出画面
        # （已实测："旧版 V4 特写 视角 等轴（与新版同）"会挤掉 X 轴标签）。
        # "与新版同相机"这层信息已由两图相同的视线方向向量本身表达，不必写进标题。
        # add_direction_banner 内部还会按可用宽度再截断一次，故长文件名不会破版。
        (rec["closeup_old_rect"], t("banner.title.closeup", label=label_old),
         closeup_dir_vec, closeup_axes),
    ]
    for fname, title, vec, axes in banner_jobs:
        bh = add_direction_banner(os.path.join(output_dir, fname), title, vec, axes)
    # 带高随图宽变化（字号按比例推导），下游要读这个字段而不是自己写死常量——
    # 任何"读加带后图片并与 bbox_2d 比对"的代码都必须把 y 下移 banner_h。
    rec["banner_h"] = bh

    return rec


def draw_move_arrow(img_path, p_from, p_to, output_path, text):
    """在图上从旧中心投影点画一根箭头到新中心投影点，旁标位移量。

    why 要画箭头：仅两张图并排时，读者要来回扫视才能看出零件挪了多少、朝哪挪。
    箭头把"位移"这件事直接画在新位置图上。

    p_from/p_to 是【原生视口坐标】，本函数按 UPSCALE_FACTOR 放大到图片像素坐标——
    与红框走同一条换算路径（坑 B3：投影坐标是原生的，图片放大了 UPSCALE_FACTOR 倍）。
    """
    import math
    import numpy as np
    from PIL import Image, ImageDraw
    img = Image.open(img_path).convert("RGB")
    draw = ImageDraw.Draw(img)
    W = img.width
    x0, y0 = p_from[0] * UPSCALE_FACTOR, p_from[1] * UPSCALE_FACTOR
    x1, y1 = p_to[0] * UPSCALE_FACTOR, p_to[1] * UPSCALE_FACTOR
    lw = max(2, int(W * MOVE_ARROW_WIDTH_RATIO))
    head = max(8, int(W * MOVE_ARROW_HEAD_RATIO))

    dist_px = ((x1 - x0) ** 2 + (y1 - y0) ** 2) ** 0.5
    if dist_px >= 3:
        draw.line([x0, y0, x1, y1], fill=MOVE_ARROW_COLOR, width=lw)
        ang = math.atan2(y1 - y0, x1 - x0)
        for sign in (-1, 1):
            a = ang + sign * 2.6
            draw.line([x1, y1, x1 + head * math.cos(a), y1 + head * math.sin(a)],
                      fill=MOVE_ARROW_COLOR, width=lw)
    else:
        # 位移在该视角下几乎沿视线方向，投影成一个点：画个圈表示"朝屏幕内外移动"，
        # 不画会让读者以为图错了。
        rr = max(6, int(W * 0.008))
        draw.ellipse([x1 - rr, y1 - rr, x1 + rr, y1 + rr],
                     outline=MOVE_ARROW_COLOR, width=lw)

    # 标签落点复用 _place_label 的空白度实测选位，避免压住零件本体
    arr = np.asarray(img).astype(np.int16)
    f = load_font(int(W * MOVE_LABEL_RATIO))
    tw = draw.textlength(text, font=f)
    th = int(W * MOVE_LABEL_RATIO * 1.25)
    pad = max(2, int(W * 0.004))
    lw_box, lh_box = tw + pad * 2, th + pad * 2
    mid_x, mid_y = (x0 + x1) / 2, (y0 + y1) / 2
    rect = (mid_x - lw_box / 2, mid_y - lh_box / 2,
            mid_x + lw_box / 2, mid_y + lh_box / 2)
    lx, ly, _ = _place_label(arr, rect, lw_box, lh_box,
                             int(W * RECT_LABEL_GAP_RATIO), [],
                             img.width, img.height)
    draw.rectangle([lx, ly, lx + lw_box, ly + lh_box],
                   fill=(255, 255, 255), outline=MOVE_ARROW_COLOR,
                   width=max(1, lw // 2))
    draw.text((lx + pad, ly + pad), text, font=f, fill=MOVE_ARROW_COLOR)
    img.save(output_path)


def render_moved_one(md, av, doc, output_dir, bn, idx, change, bbox,
                     new_obj, feats, injected_names, pre, self_names,
                     label_old=None, label_new=None):
    """渲染位移类差异（change_type == "moved"）：整体定位图 + 旧位置图 + 新位置图。

    why 需要独立分支：`moved` 没有对称差几何体，硬套 render_one 会走到
    removed/added 均为空 → 图上 0 个品红、cluster_count=0 → 红框退化 → 一连串静默降级。
    这里改为把零件【本体】用品红高亮（语义"发生位移的零件"，由标记带与 PPT 文字说明）。

    【坑 C2 对 moved 同样成立】新旧两张位置图必须共用同一相机、同一份投影坐标、
    同一截图尺寸，中间不得有任何相机操作，否则两图静默错位而不报错。
    【坑 B1/B2】一律走 capture_and_project()，箭头端点与红框同一次投影。
    """
    # 默认标签在函数体内取：签名默认值在 import 时就求值，那时 --lang 还没解析（见 i18n）
    label_old = label_old or t("label.old")
    label_new = label_new or t("label.new")
    rec = {"base_name": bn, "instance_index": change["instance_index"],
           "change_type": "moved", "label_old": label_old, "label_new": label_new}
    # moved 类的既有字段取值约定（下游必须先判 change_type 再读，见 AGENTS.md §3 契约铁律）：
    # 没有对称差 → 无簇、无增减料。写成 0 而非缺省，避免下游 KeyError。
    rec["cluster_count"] = 0
    rec["cluster_details"] = []
    rec["removed_volume"] = 0.0
    rec["added_volume"] = 0.0
    rec["translation_mm"] = change.get("translation_mm")
    rec["rotation_deg"] = change.get("rotation_deg")
    rec["rotation_detected_by"] = change.get("rotation_detected_by")

    old_center = change.get("old_center") or [0, 0, 0]
    new_center = change.get("new_center") or [0, 0, 0]

    # === (a) 整体定位图 ===
    print("  Rendering overview image (scanning candidate view directions)...")
    setup_overview_state(md, doc, injected_names, self_names, feats.get("moved_new"))
    # 【moved 特有】必须把目标零件本体隐藏，只留品红高亮体代表它。
    # why：moved 的高亮体就是零件本身的 Shape，两者【完全重合】。若同时可见，
    # 85% 透明的零件叠在不透明品红上会把颜色冲淡成低饱和紫（g 落在 60~110），
    # 于是 score_direction 的 sat_in 恒为 0、每次都走 fallback，
    # 产出"其实看得很清楚却标注成被遮挡"的图（实测 sat_in=0 而 count_magenta=17287，
    # 两个数字矛盾正是这个冲淡效应的指纹）。
    # shape_changed 不受此影响：那里的高亮体是薄片状对称差，不与零件表面完全重合。
    for nm in (self_names or ()):
        try:
            vobj = md.getObject(nm)
            if vobj is not None:
                vobj.Visibility = False
        except Exception:
            pass
    FreeCADGui.updateGui()
    scan_tmp = os.path.join(output_dir, f".scan_{bn}_{idx}.png")
    dir_tag, dir_vec, dir_detail, dir_rows = pick_best_direction(av, bbox, scan_tmp)
    try:
        os.remove(scan_tmp)
    except OSError:
        pass
    av.setViewDirection(dir_vec)
    FreeCADGui.updateGui()
    av.fitAll()
    FreeCADGui.updateGui()
    time.sleep(1)

    overview_path = os.path.join(output_dir, f"{bn}_{idx}_overview.png")
    ov_size, bbox_2d = capture_and_project(av, overview_path, bbox)
    # 整体图同时框住旧位置与新位置：只框新位置时读者看不出"从哪挪来的"。
    # 两个框的投影紧跟截图、同源尺寸（坑 B2），中间不插入任何相机操作。
    bbox_old_ov = change.get("bbox_old") or bbox
    bbox_2d_ov_old = project_bbox(av, bbox_old_ov, flip_y_height=ov_size[1])
    overview_axes = axis_screen_dirs(av)
    overview_rect_path = os.path.join(output_dir, f"{bn}_{idx}_overview_rect.png")
    ov_rects, line_w = draw_rects_on_image(
        overview_path, [(bbox_2d_ov_old, []), (bbox_2d, [])], overview_rect_path)
    rec["overview"] = os.path.basename(overview_path)
    rec["overview_rect"] = os.path.basename(overview_rect_path)
    rec["bbox_2d"] = list(bbox_2d)
    rec["rects_overview"] = [{"rect": [round(v, 1) for v in rect], "labels": labels}
                             for rect, labels in ov_rects]
    rec["rect_line_width_px"] = line_w
    rec["magenta_px_overview"] = count_magenta(overview_rect_path)
    rec["overview_view_dir"] = [round(v, 4) for v in dir_vec]
    rec["overview_view_tag"] = dir_tag
    rec["overview_view_score"] = dir_detail["score"]
    rec["overview_sat_in"] = dir_detail["sat_in"]
    rec["overview_sat_out"] = dir_detail["sat_out"]
    rec["overview_view_fallback"] = bool(dir_detail.get("fallback"))
    rec["overview_view_readability"] = dir_detail["readability"]
    rec["overview_ecc"] = dir_detail["ecc"]
    rec["overview_compose"] = dir_detail["compose"]
    rec["overview_body_pct"] = dir_detail["body_pct"]
    rec["overview_axis_dirs"] = {k: [round(c, 4) for c in v]
                                 for k, v in overview_axes.items()}
    print(f"    Overview magenta_px={rec['magenta_px_overview']}  view={dir_tag}")

    # === (b) 新位置图（先渲染，相机由它定下，(c) 复用）===
    print("  Rendering new-position close-up...")
    hide_all_objects(md, doc)
    if new_obj is not None:
        show_with_ancestors(md, new_obj)
        # 【不要 show_object_and_children + 半透明】理由同整体图：moved 的高亮体与零件
        # 完全重合，零件半透明叠在品红上会把饱和度冲淡。这里只点亮祖先容器（否则渲染全白），
        # 零件本体交给品红高亮体代表。
        set_transparency_recursive(md, new_obj, TARGET_TRANSPARENCY)
        for nm in (self_names or ()):
            try:
                vobj = md.getObject(nm)
                if vobj is not None:
                    vobj.Visibility = False
            except Exception:
                pass
    if feats.get("moved_new") is not None:
        set_highlight_style(md, feats["moved_new"])
    FreeCADGui.updateGui()
    av.viewIsometric()
    FreeCADGui.updateGui()
    av.fitAll()
    FreeCADGui.updateGui()
    time.sleep(1)

    # 取景要同时容纳旧位置与新位置，否则读者看不到"从哪挪到哪"。
    # 用两个中心点的联合 bbox 取景，而不是单个零件的 bbox。
    span = {
        "x_min": min(old_center[0], new_center[0]), "x_max": max(old_center[0], new_center[0]),
        "y_min": min(old_center[1], new_center[1]), "y_max": max(old_center[1], new_center[1]),
        "z_min": min(old_center[2], new_center[2]), "z_max": max(old_center[2], new_center[2]),
    }
    union = {
        "x_min": min(bbox["x_min"], span["x_min"]), "x_max": max(bbox["x_max"], span["x_max"]),
        "y_min": min(bbox["y_min"], span["y_min"]), "y_max": max(bbox["y_max"], span["y_max"]),
        "z_min": min(bbox["z_min"], span["z_min"]), "z_max": max(bbox["z_max"], span["z_max"]),
    }
    zx1, zy1, zx2, zy2 = project_bbox(av, union)
    zoom_pad = max(20, int(0.3 * max(zx2 - zx1, zy2 - zy1)))
    av.boxZoom(int(zx1 - zoom_pad), int(zy1 - zoom_pad),
               int(zx2 + zoom_pad), int(zy2 + zoom_pad))
    FreeCADGui.updateGui()
    time.sleep(1)

    new_path = os.path.join(output_dir, f"{bn}_{idx}_closeup_new.png")
    cu_size, bbox_2d_closeup = capture_and_project(av, new_path, bbox)
    # 两个中心点与旧 bbox 的投影必须与截图同源（同一次取到的尺寸），故紧跟 capture_and_project、
    # 中间不插入任何相机操作（坑 B2）。y 要翻转成图像上原点，与 project_bbox 内部一致（坑 B1）。
    bbox_old = change.get("bbox_old") or bbox
    bbox_2d_old = project_bbox(av, bbox_old, flip_y_height=cu_size[1])
    pc_old = av.getPointOnScreen(Vector(*old_center))
    pc_new = av.getPointOnScreen(Vector(*new_center))
    p_old_2d = (pc_old[0], cu_size[1] - pc_old[1])
    p_new_2d = (pc_new[0], cu_size[1] - pc_new[1])
    closeup_axes = axis_screen_dirs(av)
    closeup_dir_vec = tuple(av.getViewDirection())
    if not check_image_nonblank(new_path):
        print(f"    !! WARNING: new-position image is all white; "
              f"check the parent_chain/visibility logic for {bn}")

    new_rect = os.path.join(output_dir, f"{bn}_{idx}_closeup_new_rect.png")
    cu_rects, _ = draw_rects_on_image(new_path, [(bbox_2d_closeup, [])], new_rect)
    trans = change.get("translation_mm") or 0.0
    rot = change.get("rotation_deg") or 0.0
    arrow_text = f"Δ {trans:.2f} mm" + (f"  /  {rot:.1f}°" if rot > 0 else "")
    draw_move_arrow(new_rect, p_old_2d, p_new_2d, new_rect, arrow_text)
    rec["closeup_new"] = os.path.basename(new_path)
    rec["closeup_new_rect"] = os.path.basename(new_rect)
    rec["bbox_2d_closeup"] = list(bbox_2d_closeup)
    rec["rects_closeup"] = [{"rect": [round(v, 1) for v in rect], "labels": labels}
                            for rect, labels in cu_rects]
    rec["magenta_px_closeup_new"] = count_magenta(new_rect)
    print(f"    New-position image magenta_px={rec['magenta_px_closeup_new']}")

    # === (c) 旧位置图 —— 【绝对不动相机】（坑 C2）===
    print("  Rendering old-position close-up (reusing the new-position camera, view unchanged)...")
    hide_all_objects(md, doc)
    if feats.get("moved_old") is not None:
        set_highlight_style(md, feats["moved_old"])
    FreeCADGui.updateGui()
    time.sleep(1)

    old_path = os.path.join(output_dir, f"{bn}_{idx}_closeup_old.png")
    save_native_and_upscale(av, old_path, cu_size)
    old_rect = os.path.join(output_dir, f"{bn}_{idx}_closeup_old_rect.png")
    # 用【旧位置】的 bbox 投影画框——它与新位置图共用同一相机，投影早在上面同一批完成，
    # 这里不重新投影也不动相机（坑 C2）。若沿用新位置的 bbox，红框会画在品红零件【旁边】
    # ——这是本轮 PowerPoint 导出图目视发现的（坑 G1：坐标算得对不代表画面对）。
    draw_rects_on_image(old_path, [(bbox_2d_old, [])], old_rect)
    rec["closeup_old"] = os.path.basename(old_path)
    rec["closeup_old_rect"] = os.path.basename(old_rect)
    rec["bbox_2d_closeup_old"] = list(bbox_2d_old)
    rec["magenta_px_closeup_old"] = count_magenta(old_rect)
    print(f"    Old-position image magenta_px={rec['magenta_px_closeup_old']}")

    # === 数值字段 ===
    rec["old_volume"] = change.get("old_volume")
    rec["new_volume"] = change.get("new_volume")
    rec["volume_delta"] = 0.0
    rec["volume_delta_pct"] = 0.0
    rec["diff_bbox_size_mm"] = change.get("diff_bbox_size_mm")

    # === 方向标记带（F6：品红统计已在上面全部完成，加带必须在统计之后）===
    rec["closeup_view_dir"] = [round(v, 4) for v in closeup_dir_vec]
    rec["closeup_axis_dirs"] = {k: [round(c, 4) for c in v]
                                for k, v in closeup_axes.items()}
    banner_jobs = [
        (rec["overview_rect"], t("banner.title.overview", tag=dir_tag), dir_vec, overview_axes),
        (rec["closeup_new_rect"], t("banner.title.moved_new", label=label_new),
         closeup_dir_vec, closeup_axes),
        (rec["closeup_old_rect"], t("banner.title.moved_old", label=label_old),
         closeup_dir_vec, closeup_axes),
    ]
    for fname, title, vec, axes in banner_jobs:
        bh = add_direction_banner(os.path.join(output_dir, fname), title, vec, axes)
    rec["banner_h"] = bh
    return rec


def derive_label(stp_path, explicit=None, max_len=18):
    """版本标签：优先用调用方显式给的，否则取 STP 文件名（不含扩展名）。

    why 需要显式参数：线上 agent 场景下文件名可能是无意义的哈希或临时名。
    why 要截断：标记带标题过长会顶到坐标轴指示器上把轴标签挤出画面（坑 F8）。
    add_direction_banner 内部还会按可用宽度再截一次，这里只做粗截防止极端长名。
    """
    if explicit:
        label = str(explicit)
    else:
        label = os.path.splitext(os.path.basename(stp_path))[0]
    if len(label) > max_len:
        label = label[:max_len - 1] + "…"
    return label


def write_manifest(output_dir, manifest):
    """原子写 manifest：先写临时文件再 os.replace 覆盖。

    why 原子：每渲染完一处差异就重写一次（超时/崩溃后已完成的部分不丢），
    直接覆盖写在中途被 kill 会留下截断的 JSON，下游读到就是解析错误。
    why 整体重写：JSON 不支持真正的追加；差异量级（≤几十）下重写成本可忽略。
    """
    manifest_path = os.path.join(output_dir, "render_manifest.json")
    tmp = manifest_path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2)
    os.replace(tmp, manifest_path)
    return manifest_path


def run(geom_json_path, stp_old, stp_new, output_dir,
        label_old=None, label_new=None):

    os.makedirs(output_dir, exist_ok=True)

    with open(geom_json_path, "r", encoding="utf-8") as f:
        geom = json.load(f)

    lbl_old = derive_label(stp_old, label_old)
    lbl_new = derive_label(stp_new, label_new)

    actual_diffs = [d for d in geom["geometric_diffs"]
                    if d.get("geometric_changes")]

    if not actual_diffs:
        # 【必须写 manifest】"两版几何相同"是真实且常见的场景（只改了标注或元数据）。
        # 早先这里直接 return 不写文件，下游 build_pptx 的 open() 必然 FileNotFoundError，
        # 用户无法区分"没有差异"与"程序坏了"。
        # 此处 GUI 尚未初始化，也【不要】启动它——省 3s 且避免无谓弹窗。
        manifest_path = write_manifest(output_dir, [])
        summary = {
            "diff_count": 0,
            "reason": t("summary.no_geometry_diff"),
            "total_candidates": geom.get("total_candidates"),
            "label_old": lbl_old,
            "label_new": lbl_new,
            "skipped_parts": geom.get("skipped_parts", []),
            "filtered_by_threshold": geom.get("filtered_by_threshold", []),
            "global_alignment": geom.get("global_alignment", {}),
            "notes": [{"base_name": d.get("base_name"), "note": d.get("note")}
                      for d in geom["geometric_diffs"] if d.get("note")],
        }
        with open(os.path.join(output_dir, "render_summary.json"), "w",
                  encoding="utf-8") as f:
            json.dump(summary, f, ensure_ascii=False, indent=2)
        print("No geometric differences to render (wrote an empty manifest and render_summary.json)")
        print(f"Manifest: {manifest_path}")
        return

    print("Initializing GUI...")
    FreeCADGui.showMainWindow()
    time.sleep(3)

    print("Loading new-version STP...")
    doc = FreeCAD.newDocument("Render")
    Import.insert(stp_new, doc.Name)
    print(f"  {len(doc.Objects)} objects")

    print("Loading old-version STP (to compute diff geometry for color highlighting "
          "+ render the old-version side-by-side image)...")
    old_doc = FreeCAD.newDocument("RenderOld")
    Import.insert(stp_old, old_doc.Name)
    print(f"  {len(old_doc.Objects)} objects")
    time.sleep(2)

    label_map = {obj.Label: obj for obj in doc.Objects}
    old_label_map = {obj.Label: obj for obj in old_doc.Objects}

    # ===== 阶段1/3：预算（必须在任何可见性操作之前完成）=====
    print("\n[Stage 1/3] Precomputing diff geometry (must run before any visibility change)...")
    precomputed = precompute_shapes(actual_diffs, label_map, old_label_map)

    # ===== 阶段2/3：注入 =====
    print("\n[Stage 2/3] Injecting highlight bodies and old-version proxy objects...")
    injected, injected_names = inject_features(doc, actual_diffs, precomputed)
    print(f"  Injected {len(injected_names)} objects")

    md = FreeCADGui.getDocument(doc.Name)
    av = md.ActiveView
    # 注入对象默认可见，先全部隐藏，避免污染后续取景
    hide_injected(md, injected_names)

    # 目标零件子树的Name集合：整体图据此区分"目标零件"（半透明40）与"周围零件"（高透明85）。
    # 必须在预算阶段之后算——collect_subtree_names 只读 Group，不触发 A1 那个 Shape 失效问题。
    self_names_map = {}
    for d_idx, diff_entry in enumerate(actual_diffs):
        for c_idx, _ in enumerate(diff_entry["geometric_changes"]):
            key = (d_idx, c_idx)
            obj = precomputed.get(key, {}).get("new_obj")
            acc = set()
            if obj is not None:
                collect_subtree_names(obj, acc)
            self_names_map[key] = acc

    # ===== 阶段3/3：渲染 =====
    print("\n[Stage 3/3] Rendering...")
    manifest = []
    total = sum(len(d["geometric_changes"]) for d in actual_diffs)
    done = 0

    for d_idx, diff_entry in enumerate(actual_diffs):
        bn = diff_entry["base_name"]
        for c_idx, change in enumerate(diff_entry["geometric_changes"]):
            done += 1
            key = (d_idx, c_idx)
            pre = precomputed.get(key, {})
            feats = injected.get(key, {})
            bbox = change["bbox"]
            new_obj = pre.get("new_obj")
            ctype = change.get("change_type", "shape_changed")
            print(f"\n[{done}/{total}] {bn}  [{ctype}]")

            # 按 change_type 分流：moved 没有对称差几何体，硬套 render_one 会一路静默降级
            # （0 品红 → cluster_count=0 → 红框退化成不存在的并集 bbox），必须走独立分支。
            if ctype == "moved":
                rec = render_moved_one(md, av, doc, output_dir, bn, d_idx, change,
                                       bbox, new_obj, feats, injected_names, pre,
                                       self_names_map.get(key, set()),
                                       label_old=lbl_old, label_new=lbl_new)
            else:
                rec = render_one(md, av, doc, output_dir, bn, d_idx, change, bbox,
                                 new_obj, feats, injected_names, pre,
                                 self_names_map.get(key, set()),
                                 label_old=lbl_old, label_new=lbl_new)

            manifest.append(rec)
            # 每处差异渲染完就落盘：渲染是最贵的一步，超时/崩溃后已完成的部分不该丢。
            write_manifest(output_dir, manifest)

            # 恢复本轮改动的状态，避免污染下一轮取景。
            # 整体图把全部零件都改成了 CONTEXT_TRANSPARENCY，所以这里必须把全部真实几何
            # 的透明度都清零，不能只清目标零件——否则残留透明度会污染下一轮的特写图。
            for obj in doc.Objects:
                if not is_real_geometry(obj) or obj.Name in injected_names:
                    continue
                try:
                    vobj = md.getObject(obj.Name)
                    if vobj is not None and hasattr(vobj, "Transparency"):
                        vobj.Transparency = 0
                except Exception:
                    pass
            hide_injected(md, injected_names)

    manifest_path = write_manifest(output_dir, manifest)

    FreeCAD.closeDocument(doc.Name)
    FreeCAD.closeDocument(old_doc.Name)
    print(f"\nRendering complete, {len(manifest)} screenshot sets")
    print(f"Manifest: {manifest_path}")


def main(argv=None):
    import argparse
    ap = argparse.ArgumentParser(description="Render diff screenshots (FreeCAD GUI mode)")
    ap.add_argument("geom_json")
    ap.add_argument("stp_old")
    ap.add_argument("stp_new")
    ap.add_argument("output_dir")
    ap.add_argument("--label-old", default=None,
                    help="label for the old version; defaults to the STP file name. In "
                         "agent scenarios the file name may be a meaningless hash, so it "
                         "can be given explicitly")
    ap.add_argument("--label-new", default=None, help="label for the new version, as above")
    ap.add_argument("--lang", default=None, choices=i18n.available_langs(),
                    help="Language of the generated annotations (default: en)")
    args = ap.parse_args(argv)
    # 产出物语言必须在 run() 之前设定：文案是在深层渲染函数里取的（见 i18n 模块说明）
    i18n.set_lang(args.lang)
    run(args.geom_json, args.stp_old, args.stp_new, args.output_dir,
        label_old=args.label_old, label_new=args.label_new)


if __name__ == "__main__":
    main()

