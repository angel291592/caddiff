"""生成合成 STP 对：一个装配体，其中一个零件在新版里平移了 5mm，另一个零件形状变了。
用于端到端验证 moved 类的渲染与 PPT 版式（本案例真实 STP 里没有 moved 差异）。

用 FreeCAD python 运行：
    <FreeCAD>/bin/python.exe make_moved_fixture.py <outdir>
产出 <outdir>/moved_old.stp 与 <outdir>/moved_new.stp
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import fcenv  # noqa: E402  （路径解析的唯一真相源，必须先于 import FreeCAD）
import console  # noqa: E402

console.enable_utf8_output()

fcenv.inject_freecad_paths()

import FreeCAD  # noqa: E402
import Import  # noqa: E402
import Part  # noqa: E402


def build(doc, shift_mm, shrink):
    """搭一个 3 零件的装配体。
    BASEPLATE 恒定；SLIDER 在新版里平移 shift_mm；BRACKET 在新版里被削掉一块。
    零件名故意用"字母+数字"结尾（BRACKET-01 / SLIDER-02），同时也是 base_name
    归并规则的反例素材。
    """
    plate = doc.addObject("Part::Feature", "BASEPLATE-01")
    plate.Shape = Part.makeBox(60, 40, 4)

    slider = doc.addObject("Part::Feature", "SLIDER-02")
    s = Part.makeBox(10, 10, 8)
    s.translate(FreeCAD.Vector(5 + shift_mm, 15, 4))
    slider.Shape = s

    bracket = doc.addObject("Part::Feature", "BRACKET-03")
    b = Part.makeBox(12, 12, 10)
    b.translate(FreeCAD.Vector(40, 14, 4))
    if shrink:
        cut = Part.makeBox(4, 12, 4)
        cut.translate(FreeCAD.Vector(40, 14, 10))
        b = b.cut(cut)
    bracket.Shape = b

    doc.recompute()
    return [plate, slider, bracket]


def main():
    outdir = sys.argv[1] if len(sys.argv) > 1 else HERE
    os.makedirs(outdir, exist_ok=True)

    # 【两版必须用同一个文档名】STEP 导出会把文档名写进顶层 PRODUCT 记录，而 BOM 层是按
    # PRODUCT 名比对的——两版名字不同会凭空造出 added / removed 各一条，让「只改了两个零件」
    # 的样例看起来像「整个装配体被换掉」。实测过：MovedOld vs MovedNew 会多报 2 处 BOM 差异。
    doc_name = "MovedFixture"

    d_old = FreeCAD.newDocument(doc_name)
    objs_old = build(d_old, shift_mm=0, shrink=False)
    p_old = os.path.join(outdir, "moved_old.stp")
    Import.export(objs_old, p_old)
    # 必须先关掉旧文档再建同名文档：同名文档在同一个进程里共存时 FreeCAD 会自动加后缀
    # （MovedFixture001），那等于白改。
    FreeCAD.closeDocument(d_old.Name)

    d_new = FreeCAD.newDocument(doc_name)
    objs_new = build(d_new, shift_mm=5.0, shrink=True)
    p_new = os.path.join(outdir, "moved_new.stp")
    Import.export(objs_new, p_new)
    FreeCAD.closeDocument(d_new.Name)

    print("written:", p_old)
    print("written:", p_new)
    print("Expected: SLIDER is moved (5 mm translation), BRACKET is shape_changed "
          "(192 mm³ removed), BASEPLATE unchanged")


if __name__ == "__main__":
    main()
