# 能力一：STP 装配体差异对比流水线（`caddiff/`）— 模块功能文档

> 本文是 diff 能力线的**唯一详细文档**：读它即可修改/调试 `caddiff/` 下全部脚本。
> 项目宪法（命名/契约铁律/决策记录）见 [`../AGENTS.md`](../AGENTS.md)；对外用法见 [`../README.md`](../README.md)。
> 坑编号（A~I、G1~G7）沿用历史编号，跨文档引用不重排。

---

## 0. 能力与状态

输入两个 STP（同一装配体新旧两版），在几何层计算差异 → 品红高亮 + 红框标注渲染 → 生成差异清单报告（HTML / Markdown）与机器可读 manifest（JSON），`--pptx` 时另出对比 PPT。

- **状态**：PoC 完成并端到端验证（173/173 断言）；多差异点标框改动 55/55；通用化改造 **P0（健壮性）已完成（18/18 断言，2026-08-27）**；**P1（配对正确性）与 P2（规模化）未实施**。
- 产出：`<out>/report.html`（人读报告）+ `report.md`（PR 评论用）+ `diff_manifest.json`（机器契约），差异图在 `<out>/images/*.png`；`--pptx` 时另出 `compare.pptx`，3 页，每页三图横排（旧版特写 / 新版特写 / 整体定位）+ 下方结构化信息带（左栏数值：差异尺寸/减料/增料；右栏定性：整体图视角；底部图例）。每张图顶部带方向标记带（标题 + 视线方向向量 + 三色坐标轴箭头，字号按图宽等比），整体定位图视角逐差异自动选。同一零件多处空间分离差异各自画框，圈号（①②…）标在框外空白处、引出线连回框边，左栏按簇列尺寸。代码全在 `caddiff/` 下，不改动原始 STP/PPT。
- **P0 已交付的通用性能力**：无差异时正常出说明页不崩溃；装配位置/姿态变化作为 `moved` 类检出并单独呈现（此前静默漏检）；布尔运算移入子进程可硬超时；版本标签由文件名或 CLI 推导；被跳过/未匹配/被阈值筛掉的零件一律显式落进产物与 PPT。

---

## 1. 核心技术判断（why）

- **STEP 文本不能直接 diff**：B-rep 实体按内部编号线性排列，同一几何重新导出编号就全变，文本 diff 全是假差异。差异必须在几何层（装配树结构 + 布尔运算）计算。
- **BOM diff 用字符串规则而非几何**：零成本、100% 确定。零件名末尾会被 Creo 追加实例编号段（如 `AA-GLUE_1_1_2_3_1_1` → `..._1`），正则截掉末尾非字母部分得到 `base_name` 作为**族名**。**是否真的折叠由两版联合决定**（`bom_diff.align_keys`）：该族在两版的名字集合完全相同时用**精确名**，否则才折叠——单看名字区分不了「同一零件的实例编号」与「同前缀的不同零件」，误折叠会让整组真实零件被容器判定吞掉（坑 J1）。
- **几何 diff 只对 BOM 筛出的"名字对得上"的候选做对称差**（`(A-B)∪(B-A)`），避免对全部零件做无差别布尔运算。
- **"体积与 bbox 都没变"不等于"没有差异"**：这两个量对平移完全不变、对 90°/180° 旋转部分不变。实测 `box` 与 `box 平移 50mm` 会被判为无差异跳过，而真实对称差达 400mm³（零件体积 2 倍，两形状零重叠）——**装配位置调整类改动此前完全漏检且不报告**。故改为三态分类：`identical`（唯一可静默跳过的档）/ `moved`（记位移与旋转，不做布尔）/ `shape_changed`（走对称差）。`moved` 判据刻意从严（体积**与** bbox 三边长排序后**均**一致），误判会落到 `shape_changed` 这个安全侧。
- **红框坐标必须来自精确的 3D bbox 投影计算，不能靠"看图猜差异位置"**：框住哪里是可复算的几何量，才能被断言。
- **必须用颜色高亮差异几何体本身，红框只是辅助**：三处差异体积只占零件自身体量 0.35%~3.97%，这种量级的表面/边缘形变在灰色渲染下"框住一片区域"人眼分辨不出。把对称差几何体用品红不透明叠加在半透明零件上，才能一眼可见。
- **红框必须逐"差异簇"画，不能画并集 bbox**：对称差常由多块空间分离的材料构成，取整体 BoundBox 会退化成"一个大框套住整个零件"，框失去定位意义。实测 PART-A 的增料是两块，中心相距 18.3mm 而零件本身仅约 19mm 宽，并集 bbox 正好等于整件——撑大它的那块只占差异体积 16%。做法是把 `removed`/`added` 按 `Shape.Solids` 拆簇、逐簇投影画框，投影后重叠的框再合并（详见 §6 H 组坑）。
- **对称差要拆成 removed/added 两半**：`removed`（旧有新无=被去掉的材料）高亮在旧版图，`added`（新有旧无=新增的材料）高亮在新版图，两图同相机渲染实现并排对比。语义差别由报告 / PPT 的文字带说明，不靠颜色区分，避免读者记色标。
- **整体定位图的视角必须逐差异自动选，不能固定等轴**：固定 `viewIsometric()` 与特写图同向，差异高亮多半被自身或兄弟零件挡死，这张图的信息量就归零。做法是把非目标零件设 `CONTEXT_TRANSPARENCY=85`，再扫描 26 个候选方向按综合评分选优（详见 F 组坑）。
- **选向不能只按品红像素数排序**：那样会选出"品红最多但画面是线框糊团"的正交侧视，装配体认不出来。必须叠加可读性因子，且**可读性不达标时宁可放弃品红、保画面**（整体图的职责是定位，差异细节由特写图承担）。

---

## 2. 技术架构

```
OLD.stp, NEW.stp
   │
   ├─ [1] bom_diff.py（系统Python，纯文本解析STEP的PRODUCT实体）
   │     → bom_diff.json（增/删/候选分类）
   │
   ├─ [2] geom_diff.py（FreeCAD Python，console模式）
   │     Import.insert加载两版 → 配对 → classify_change 三态分类
   │       ├ identical      → 跳过（唯一可静默跳过的档）
   │       ├ moved          → 不做布尔，记位移量/旋转角
   │       └ shape_changed  → 布尔对称差（在【子进程】里跑，可硬超时 kill）
   │     → geom_diff.json（bbox世界坐标 / parent_chain / 体积数值字段 /
   │                       change_type / skipped_parts / global_alignment / settings）
   │
   ├─ [3] render_diff.py（FreeCAD Python，GUI模式）
   │     预算(precompute) → 注入(inject) → 渲染(render)  ← 顺序是硬约束
   │     shape_changed 出3张图（整体定位 / 新版特写 / 旧版特写）
   │     moved 出3张图（整体定位 / 新位置 / 旧位置 + 位移箭头）
   │     → PNG（落在 `<out>/images/`）+ render_manifest.json（含品红像素自检数；每处差异渲完即原子落盘）
   │
   └─ [4] 报告拼版（系统Python）
         先落 diff_manifest.json（机器契约，含 summary）
         按 change_type 分派两套版式；空 manifest 出"未检出差异"说明页
         → report.html / report.md；--pptx 时额外 → compare.pptx
```

**布尔运算必须在子进程里跑（不可回退成直接调用）**：FreeCAD 的布尔是 C++ 阻塞调用，Windows 上 `signal.alarm` 不可用、线程也中断不了它。实测 `PART-B` **仅 912 面**却单次 `cut` 跑过 **840s 未返回**——面数与布尔耗时不成正比，任何面数闸门都拦不住它。`boolean_worker.py` 通过 `exportBrep`/`importBrep` 只传单个零件几何（正常零件端到端仅 0.6s 开销，不需要在子进程里重新导入 STP），父进程超时即 `kill`，零件记入 `skipped_parts`。**子进程里必须先 `import FreeCAD` 再 `import Part`**，直接 import Part 会以 0xC0000005 访问违例崩溃（实测 `rc=3221225477`）。

**环境约束（必须遵守）**：`geom_diff.py` 和 `render_diff.py` 必须用**能 `import FreeCAD` 的解释器**运行——`caddiff/fcenv.py` 是解释器路径解析的**唯一真相源**（`FREECAD_PYTHON` → `FREECAD_HOME` → Linux 常见安装路径 → `PATH`，找不到就抛错并给修复指引，**不静默回退到当前解释器**）。`bom_diff.py` 和 `build_pptx.py` 是纯逻辑，用系统 Python。

判据是「**那个解释器能跑 `script.py args`**」，不是「它叫什么名字」——两者会分叉，实测踩过：

- **Windows**：FreeCAD 发行版的 `bin/python.exe` 就是真解释器，直接用。它的 Part/Gui 是绑定 py3.11 的编译扩展，装不到系统的 3.14。
- **Linux（apt / 官方 PPA）**：`/usr/lib/freecad/bin/freecad-python3` **不是解释器**，而是一份内嵌 Python 的 113KB **GUI 应用**。把脚本交给它，位置参数会被当成「要打开的文档」，于是它启动整个 GUI 后**永不返回**（实测流水线第二步 300s 超时 `rc=124`，容器 CPU 0%、零输出，看起来像卡死）。正确做法是系统 `python3` + `PYTHONPATH=/usr/lib/freecad/lib`——镜像里就是这么配的。上面那条「只兼容绑定版本 Python」只对 Windows 发行版成立：Linux 包把模块装在该目录下，distro 的 python3.12 可以直接用。

`deploy/Dockerfile` 把这条约束钉成了构建期断言，且断言的不只是 `-c` 能 import，还包括**脚本文件**能执行、以及虚拟显示下能取到 `ActiveView`——前一条正是用来拦住上面那个坑的（`freecad-python3` 能过 `-c`，但过不了「执行脚本文件」）。

---

## 3. 运行方式

```bash
# 一条命令跑完整流水线（系统Python 入口，内部自动切换 FreeCAD Python 跑步骤 2/3）
caddiff diff examples/demo_old.stp examples/demo_new.stp -o caddiff-out
# 实测耗时约 355s（BOM 0s / 几何 155s / 渲染 197s / PPT 1s）
# 几何步骤比早期的 89s 变慢，是因为不再硬编码跳过 PART-B：它现在真跑一次布尔并在 60s 超时
# （+62s），另有 BREP 落盘开销。这是"换任意 STP 不挂死"的代价，不是性能退化。

# 选项（全部有合理默认值）
#   -o, --out DIR                   输出目录（默认 ./caddiff-out）
#   --label-old A --label-new B     图上与报告里的版本标签（默认取文件名；文件名是哈希时必须给）
#   --lang {en,zh}                  产出物语言，默认 en（英文优先）
#   --pptx                          额外导出对比 PPT（默认不导出）
#   --max-faces 5000                超过该面数直接跳过布尔（廉价事前闸门）
#   --boolean-timeout 60            单次布尔的硬超时秒数
#   --skip-parts NAME1,NAME2        显式跳过指定零件（按 base_name 精确匹配）
#   --min-diff-pct 0.1              相对阈值：差异须大于零件体积的该百分比（默认 0 不启用）
#   --version / --help
```

产物全部落在 `<out>` 下：`diff_manifest.json`（机器契约，含 `summary`）、`report.html`、`report.md`、`images/*.png`（差异图）、`images/render_manifest.json`（**渲染模块与报告模块之间的中间契约**：逐处差异的图片文件名与像素自检数，字段契约见 §8），中间产物 `bom_diff.json` 与 `geom_diff.json`；`--pptx` 时另有 `compare.pptx`。

退出码是**对外契约**，可直接当 CI 闸门：`0` = 两版无差异 · `1` = 检出差异 · `2` = 执行失败（输入不可读 / 子进程超时或崩溃 / 内部错误）。

单步调试时各脚本的参数（注意步骤 3 是 **4 个参数**，需要新旧两个 STP）：

```bash
python caddiff/bom_diff.py <stp_old> <stp_new> <bom_diff.json>
<FreeCAD>/bin/python.exe caddiff/geom_diff.py <bom_diff.json> <stp_old> <stp_new> <geom_diff.json>
<FreeCAD>/bin/python.exe caddiff/render_diff.py <geom_diff.json> <stp_old> <stp_new> <images_dir>
python caddiff/build_pptx.py <render_manifest.json> <compare.pptx>
```

**合成用例（`moved` 类的唯一端到端验证途径）**：真实 STP 里三处差异全是 `shape_changed`，`moved` 分支在本装配体上跑不到。用 `make_moved_fixture.py` 生成一对合成 STP（SLIDER 平移 5mm / BRACKET 减料 192mm³ / BASEPLATE 不变）来验：

```bash
<FreeCAD>/bin/python.exe caddiff/make_moved_fixture.py _fixture
caddiff diff _fixture/moved_old.stp _fixture/moved_new.stp -o _fixture/out2 \
       --label-old REV-A --label-new REV-B
```

---

## 4. 实测基线（回归时拿这些数字对表）

**Windows（2026-08-27 复测）**：三处差异都是**纯增料或纯减料，不是混合**——这个事实直接决定验证断言怎么写：

| 零件 | new对象TypeId | 减料mm³ | 增料mm³ | 差异占比 | 差异簇数 | 特写图框数 | 整体图视角 | 整体图框内饱和品红 | 新版特写 | 旧版特写 |
|---|---|---|---|---|---|---|---|---|---|---|
| PART-C | Part::Feature | 1.6705 | 0 | 0.35% | 2（1.1591 / 0.5113） | 1（两簇投影重叠而合并） | `-1-1-1`(fallback) | 0（藏在内部，靠红框定位） | 0（无增料） | 68628 |
| PART-D | Part::Feature | 0 | 8.3391 | 3.97% | 1 | 1 | `-1+1+1` | 172 | 56803 | 0（无减料） |
| PART-A | **App::Part** | 0 | 4.8658 | 1.41% | 2（4.1018 / 0.7638） | **2**（各自成框） | `-1+1+1` | 199 | 5388 | 0（无减料） |

> `magenta_px_overview` 是 `count_magenta`（g<110）的计数，会随非目标零件的半透明混色轻微波动（实测 PART-A 在两次运行间为 1733 / 1696），**不适合做严格相等断言**；要断言就用 `overview_sat_in`（全饱和，实测稳定为 0 / 172 / 199）或"哪侧有体积哪侧就有品红"。

各差异簇的实测尺寸（`cluster_details.size_mm`，回归时对表）：

| 零件 | 簇① | 簇② |
|---|---|---|
| PART-C | 0.45×3.40×2.55（减料） | 0.84×5.89×0.20（减料） |
| PART-D | 4.06×4.06×3.00（增料） | — |
| PART-A | 1.29×10.97×1.23（增料） | 0.51×3.01×0.56（增料） |

全部图片的"**全饱和**品红溢出红框量"均为 **0px**。

**Linux 软件渲染基线（2026-08-28 实测，Linux 服务器，xvfb + Mesa）**：

| 零件 | 减/增 | overview_sat_in | magenta_px_overview | view_tag | fallback | closeup_new_px | closeup_old_px |
|---|---|---|---|---|---|---|---|
| PART-C | 减 1.6705 | 0 | 4 | -1+1+1 | true | 0 | 20530 |
| PART-D | 增 8.3391 | 212 | 1863 | +0-1+1 | false | 2085 | 0 |
| PART-A | 增 4.8658 | 78 | 710 | -1+0+1 | false | 2159 | 0 |

- 定性判据「哪侧有体积哪侧有品红」「sat_in 作位置判据、fallback 分档」在 Linux 下依然成立（减料件 sat_in=0 且 fallback=true，两个增料件 sat_in>0）。
- 像素绝对值与 Windows 差异大（Mesa 软件渲染 vs GPU，且 xvfb 1280 屏下输出图更小，`rect_line_width_px=8` vs Windows 12、`banner_h=203`），**不可复用 Windows 数字做严格相等断言**。
- 选向结果不同（PART-D `+0-1+1`、PART-A `-1+0+1`，Windows 均 `-1+1+1`）属预期，印证已知限制「magenta_px 随非目标零件半透明混色波动」。

**服务器端到端实测（2026-08-28，真实 STP 对）**：

| 项 | 结果 |
|---|---|
| 退出码 | 1（检出差异） |
| `diff_count` | 3（shape_changed=3） |
| 差异零件 | PART-C / PART-D / PART-A |
| 体积数值 | 与 Windows 基线**精确一致**（减料 1.6705 / 增料 8.3391 / 增料 4.8658） |
| 簇数 | 2 / 1 / 2 |
| skipped_parts | PART-B timeout(912面) + 顶层装配体 too_complex(8553面) |
| unresolved | PART-E no geometry match |
| global_alignment | misaligned=false，61 对配对，中位位移 0mm |
| 耗时 | BOM 0s / 几何 157s / 渲染 135s / PPT 1s，**总计约 290s** |
| PPT（`--pptx`） | 3 页，2.0MB |

**验证断言必须这样写，否则会产生假失败**：
- 判据是"**哪侧有非零体积、哪侧就必须有品红**"，不能一律要求两侧都有。纯减料的零件在新版图上 0 个品红是正确结果（材料只在旧版存在）。
- **位置类判据只认全饱和品红（g<60），不能用 `count_magenta` 的 g<110 阈值**：非目标零件设 85 透明度后，半透明透出的低饱和混色（如 `(182,112,205)`）会溢出红框，那是赝像不是错误。实测按 g<60 统计，三张整体图框外恒为 0。
- 整体图**不能一律断言"有品红"**：差异藏在装配体内部时（PART-C 即如此），26 个方向里任何"画面看得懂"的方向都拍不到它，此时代码走 fallback 分支、`overview_view_fallback=True`，整体图纯靠红框定位。断言应分两档写。
- 整体图的画面质量判据用 `overview_compose`/`overview_ecc`/`overview_body_pct`，**不能用 `overview_view_readability`**：后者含 solidity（品红最大连通块占比）因子，fallback 档位下没有品红、solidity 恒为 0，readability 必然被压到下限 0.15，拿它判断"画面可读"永远不成立。

---

## 5. 踩过的坑（全部经实测锁定，改代码前必读）

共同特征：**代码不报错、产物却是错的**，且**症状高度误导**——多个坑都表现为"红框/高亮位置不对"，但根因彼此无关。因此结论一律以**实测像素**为准，不接受"读代码觉得对"或"跑通没报错"。

### A. FreeCAD 对象模型层（最反直觉，A1 是本项目耗时最长的一个）

**A1. `App::Part` 容器的 `.Shape` 属性会在任何一次 Visibility 写操作后永久消失。**
二分实测：`[baseline] OK V=346.0779` → `[给该容器的子对象设 Visibility=False] FAIL AttributeError: 'App.Part' object has no attribute 'Shape'` → `[把 Visibility 改回 True] 仍 FAIL`。`hasattr(obj,'Shape')` 由 True 变 False，不是取值抛错而是属性本身被摘掉，且**不可逆**。只影响 `App::Part`，`Part::Feature` 不受影响。

后果极隐蔽：pipeline 第 1 轮渲染整体图时会给全部真实几何对象写 `Visibility=True`，其中包含 PART-A 那个 `App::Part` 容器 → 属性当场被摘掉；等第 3 轮才取 Shape 只能拿到 None → 高亮体静默缺失、图里 0 个品红却不报错。

**修法**：`run()` 严格分三阶段 预算 → 注入 → 渲染，**全部几何读取必须整体早于全部可见性写入**。这是硬约束，不是代码组织偏好。

**别再试这条错路**：曾试"容器 Shape 取不到就融合其 Group 子对象 Shape"兜底，实测对称差算出 687.25mm³（≈341+346，两形状零重叠）——子对象原始 Shape 处于容器局部坐标系，且 `容器Placement × 子Placement` 复原出的 bbox 与容器 Shape 的 bbox 实测不一致。

**A2. 子对象要渲染出来，其所有祖先 `App::Part` 容器必须都可见**，否则渲染出全白图。`show_with_ancestors()` 沿 `obj.InList` 上溯设可见。但**不要把顶层装配体容器也设可见**——`App::Part` 一旦可见会把自己的聚合 Shape 当独立实体叠加渲染，等于把整个装配体重复画一遍污染画面。

**A3. `App::Part` 容器的 ViewProvider 没有 `Transparency` 属性**（只有内部 `Part::Feature` 子对象有），设透明度必须递归下探到真正的几何对象。

**A4. 每个 `App::Part` 自带一套 Origin 基准几何**（X/Y/Z 轴 + XY/XZ/YZ 基准面，本 STP 里 16 个装配体共 112 个此类对象），默认 `Visibility=True`，且延伸很远。无差别把 `doc.Objects` 全设可见会让 `fitAll()` 把它们计入取景，模型被挤成画面中间一个小点。**修法**：`NON_GEOMETRY_TYPES` 过滤（`App::Origin`/`App::Line`/`App::Plane`/`App::Point`）。

### B. 坐标系与截图层（三个坑叠在一起，症状全都是"框对不上内容"）

**B1. `getPointOnScreen()` 是下原点(y向上)，Pillow 图像是上原点(y向下)，必须翻转 `y' = 视口高 - y`。**
**为什么能长期潜伏**：只要取景是 `fitAll`（模型居中），投影出的 y 区间就几乎对称于视口中线——实测三处分别为 `36..414`、`25..425`、`74..375`，上下界之和都≈视口高 450——翻转前后数值几乎一样，红框看着是对的。只有 `boxZoom` 聚焦到**偏离画面中心**的局部时才暴露，且偏移量随聚焦程度放大（不聚焦时上沿溢出 7px，留 30% 边距聚焦时 46px）。
**实测指纹**：未翻转时**只有上沿溢出**（左/右/下均为 0）。"溢出只发生在单侧、且溢出量随缩放变化"是坐标系原点错误的典型特征；而整体平移错位会让对侧同时出现等量余量。
**注意**：`boxZoom()` 吃的是视口坐标（与 `getPointOnScreen` 同一套下原点系），调它时**故意不做 y 翻转**。翻转只用于把坐标对齐到图像像素，两处用途不同，别统一。

**B2. 视口尺寸 `av.getSize()` 会在运行过程中变化**（实测同一次运行内 `(600,450)→(603,450)`，跑批时出现过 `603→609`）。而 `saveImage(path,w,h)` 严格按传入尺寸出图并线性缩放画面，`getPointOnScreen` 又总是相对"调用当时"的视口。三者不同源就让红框水平错位几像素。
**修法**：`capture_and_project()` 把"取尺寸→截图→投影"做成**原子操作**，尺寸只取一次两处共用，中间不插入任何触发布局重算的调用（`fitAll`/`boxZoom`/`updateGui`），并在前后各读一次尺寸、不一致就告警。

**B3. 投影坐标是原生视口坐标，而输出图片放大了 `UPSCALE_FACTOR` 倍**，画框前必须把坐标同比例放大，否则框缩在图片左上角。（早期还犯过 `saveImage` 传硬编码 1920×1080 的错，导致红框整体偏移缩到画面一角。）

### C. 渲染状态污染层

**C1. 整体图会重新点亮前几轮注入的高亮体。**"显示所有真实几何对象"的循环无差别遍历 `doc.Objects`，而前几轮 `addObject` 注入的高亮体也在其中。实测证据：`PART-D` 与 `PART-A` 两张整体图的品红像素数和 bbox**完全相同**（89 个、同一坐标），后者显示的其实是前者的残留，指向了错误的零件。
**修法**：全部注入对象登记进 `injected_names`，整体图渲染时跳过，只显式点亮当前差异自己的 `DiffHL_*`；每轮末尾把注入对象重新设不可见、目标零件透明度恢复 0。

**C2. 新旧特写图靠"绝对不动相机"实现像素级对齐。**旧版特写图必须复用新版特写图结束时的相机状态，不调 `fitAll`/`boxZoom`/`viewIsometric`，且用同一份投影坐标画框、同一个尺寸截图。**若未来有人在这两步之间插入任何相机操作，两图会静默错位**，而不会报错。

### D. 几何配对与取景层（"框是对的，但框住的东西本身就不该被比较"）

**D1. 配对算法曾把两个不相关的零件比在一起。**`pair_by_proximity()` 原来只按 bbox 中心距离做贪心最近邻，且候选集合只收 `Part::Feature`。实测新版 STP 里 `PART-A-703` 被 Creo 重构成了 `App::Part` 子装配容器（`build_label_map` 有收集 `App::Part`，但 `pair_by_proximity` 内部又按 `TypeId=="Part::Feature"` 过滤了一次，等于白收集），于是体积 341mm³ 的旧版壳体被错配到体积 75mm³ 的另一个不相关薄片上，产生的"416mm³ 差异"是纯数字伪影，而真正的 703 部件被静默漏检、不报错。
**修法**：`get_shape()` 统一取 Shape（`App::Part` 可直接访问其 Group 聚合 Shape，实测 346.08mm³ 与旧版 341.19mm³ 高度吻合，确认是同一物理部件跨版本被重构）；配对改为**距离+体积相似度联合评分**（体积差>50% 直接排除候选）；配不上的旧对象由 `unpaired_old_labels` 显式记录，不静默丢弃。修复后该部件真实差异降到 4.87mm³。

**D2. 特写图不能只 fitAll 到整个零件**，否则几 mm 的局部变化淹没在几十 mm 的零件里。**修法**：先 `fitAll` 定位，再用 `av.boxZoom()` 把当前投影出的差异 bbox（留 30% 边距）聚焦放大。`boxZoom` 签名是 `boxZoom(x1,y1,x2,y2)` **四个 int 参数，不接受 tuple**（已实测确认）。

### F. 整体图自动选向

**F1. 遮挡的根因是"非目标零件不透明"，不是"没有好视角"。**
此前整体图只把目标零件设半透明，其余零件全不透明。实测 26 方向 × 4 档上下文透明度：

| 零件 | ctx=0 | ctx=50 | ctx=70 | ctx=85 |
|---|---|---|---|---|
| PART-C | 0/26 方向可见 | 0/26 | 3/26 | 7/26 |
| PART-D | 19/26 | 19/26 | 19/26 | 19/26 |
| PART-A | 26/26 | 26/26 | 26/26 | 26/26 |

取 `CONTEXT_TRANSPARENCY=85`。这条把"整体图 0 品红"从既定限制变成了可解问题。

**F2. 只按品红像素数选向会选出线框糊团图。** 实测 PART-C 按像素数排序的冠军是正交侧视 `-1+0+0`（156px），但那张图实体面积占比仅 0.028、品红碎成 16 个连通块，装配体完全认不出来。故引入可读性因子，且**两级选择**：先在"品红真看得见（≥30px）且可读性达标（≥0.25）"的候选里选综合分最高；该档为空则退为"选画面最清楚的方向"并置 `fallback=True`，靠红框定位。

**F3. 可见性门槛不能写"非零"。** 同一差异在 `-1+1+0` 方向恰好有 **1** 个全饱和品红像素——数值非零但在 603×450 视口里就是一个点，肉眼看不见。若门槛只写 `>0`，这种方向会挤掉正确的 fallback 判定，产出一张"看着什么都没有却不作任何说明"的图。故设 `SCORE_MIN_VISIBLE_PX=30`。

**F4. 区分"模型撑满画面"与"斜躺一条"要用离心率，不能用外接框填充率。**
这是走过的一条弯路：先用"内容外接框填充率"，实测**反向且无区分度**——斜躺视角 0.398、等轴基准 0.656，按它排反而选中斜躺的。改用内容像素分布的离心率（协方差主次轴标准差之比）才真正分开：斜躺的四个方向 ecc=2.41~2.50，其余 1.04~2.02，等轴基准 1.51。故 `SCORE_ECC_LIMIT=2.2`，**超限直接判 0 分而非打折**（这类构图明确不可接受，不是"稍差一点"）。

**F5. 方向标记带只标世界坐标轴，不标"前/后/左/右/俯视"。**
STP 里哪个轴朝上、哪面是正面，取决于建模者的坐标系约定，本项目无从得知。标世界轴方向 + 三色箭头指示器是客观事实，编个"正视图"出来是臆测。轴的屏幕方向用 `getPointOnScreen` 实测投影得出，与红框坐标同源，不会出现"标记说的方向和画面实际方向不一致"。

**F6. 品红像素统计必须在加标记带之前完成。** 标记带会把图片高度增加 `banner_h`（随图宽变化），加带后再统计，红框坐标与像素坐标就整体错位了。同理，任何读取加带后图片并与 `bbox_2d` 比对的代码（如验证脚本），都必须把 y 坐标下移 manifest 里的 `banner_h` 字段——**不要写死常量**，带高是按图宽等比算出来的。

**F7. 图内字号与线宽必须按图宽等比推导，绝不能写死像素。**
三张图在 PPT 里每张只占约 4 英寸宽，1827px 的图显示 DPI 约 450。同一个病根犯过两次：
- 字号：初版按固定像素写死（标题 40px/副行 27px/轴标 24px），实际显示只有 **6.4pt / 4.3pt / 3.9pt**，用户反馈"太小看不清"。现按图宽比例定（`BANNER_TITLE_RATIO=0.062` ≈ 18pt、`BANNER_SUB_RATIO=0.042` ≈ 12pt）。
- 红框描边：`RECT_WIDTH=3` 写死，实测只有 **0.48pt**，用户反馈"线太细看不清"。现按 `RECT_WIDTH_RATIO=0.0069` → 12px → **1.91pt**。描边是向**外**扩展的（`x1-offset`），加粗只占外侧白边，不会盖住差异高亮本身。

换分辨率也不会失配。验证脚本对两者都有硬断言（标题≥12pt、副行≥8pt、描边≥1.5pt），改比例后不会静默退化。

**F8. 标记带标题要短，且必须做溢出截断。** 字号放大后，长标题会顶到右侧坐标轴指示器上把轴标签挤出画面（实测"旧版 V4 特写 视角 等轴（与新版同）"会挤掉 X 轴标签）。`add_direction_banner()` 内已按可用宽度截断加省略号。"与新版同相机"这类信息由两图相同的视线方向向量本身表达，不必写进标题。

### G. PPT 版式层（`build_pptx.py`）

**G1. 版式必须用 PowerPoint 导出图目视确认，不能只看 python-pptx 的坐标数字。**
坐标算得对不等于观感对。本轮三处问题全靠导出图才发现：图例行被裁掉下半截、右栏长文案把图例挤出页面、图题与图内标记带重复。导出方法（本机有 PowerPoint，无 LibreOffice）：
```python
app = win32com.client.Dispatch("PowerPoint.Application")   # 需 pip install pywin32
pres = app.Presentations.Open(abs_path, WithWindow=False, ReadOnly=True)
for i, s in enumerate(pres.Slides, 1):
    s.Export(os.path.join(outdir, f"page{i}.png"), "PNG", 1920, 1080)
```

**G2. 一切字号必须用 `Pt()` 显式给定。** 空白版式 `slide_layouts[6]` 的默认字号是 18pt，继承会让信息带过大挤出页面。验证脚本对"未显式设字号的 run"有硬断言。

**G3. 分色分粗细必须用 run 级别设置。** 段落级只能整段统一，做不出"标签灰 + 数值黑加粗"的效果（`put_runs()` 封装了这件事）。

**G4. 信息带高度要按【行数最多的那一页】留，不是按当前页。** 演进过两轮：`BAND_H=1280000` 时第 1 页（右栏文案较长）的图例行被挤出页面下沿 → 1620000 三页都放得下（当时左栏恒为 3 行）；加入逐簇尺寸后，多差异点的页面左栏变成 `1(共N处) + N(逐簇明细) + 2(增减料)` 行，N=2 时共 5 行，1620000 下"新增材料"那行直接压在图例上（导出图确认），故取 **1780000**。行高按 12.5pt × line_spacing 1.15 + space_after 5pt ≈ 246062 EMU/行估算。图例行单独预留 `LEGEND_H`。加高前必须校验图片是否受列宽限制：三联图 aspect 1.10 > `col_w/img_area_h`，所以图片显示宽度恒为 `col_w`（4.043in），`BAND_H` 只要小于约 1.97M 就不影响图区尺寸，也不影响线宽的 pt 换算。

**G5. 标题条下方不要画整幅宽的分隔线。** 曾在 `MARGIN_EMU+TITLE_BAR_H-150000` 处画过一条细矩形作视觉收边，那个位置正好压在标题第二行（"零件体积 / 变化量"）的下缘上把文字遮住（用户反馈）。标题的收边改由零件名自身的下划线承担——不占额外垂直空间，也不可能压到别的文字。任何"在文字附近画装饰性色块/线条"的改动，都必须导出成图确认没压到文字，坐标算得对不代表不重叠。

**G6. PPT 不要重复图内标记带已有的信息。** 图片自带"旧版 V4 特写 + 视线方向向量"，PPT 再放一行"旧版 V4 · 特写"就是重复，实测导出图里两处文字上下紧贴、显得啰嗦。已取消独立图题行（`CAPTION_H=0`），高度让给图区。

**G7. 左栏窄（33% 版面），多项内容必须每项独占一段，不能挤在一行里。** 逐簇尺寸初版写成 `① 1.29×10.97×1.23   ② 0.51×3.01×0.56 mm` 一行，实测换行点落在"② 0.51×..."中间，圈号被孤零零留在上一行行尾，读者无法把它和尺寸对应。改为每簇一段（缩进小圆点 + `① 增料  1.29 × 10.97 × 1.23 mm`）。同理，**图例行文案改长前要先量**：把"红框＝差异位置辅助定位框"改成"红框＝各处差异分别定位（①②… 对应左栏尺寸清单）"后整行挤成两行、末尾"（X红/Y绿/Z蓝）"溢出被裁。

### H. 多差异点分别标框

**H1. 对称差可能由多块空间分离的材料构成，画并集 bbox 会退化成"大框套整件"。**
实测三处差异按 `Shape.Solids` 拆簇：PART-C 减料 2 块、PART-D 增料 1 块、PART-A 增料 2 块。PART-A 那两块中心 x 分别 +17.89 与 −0.38（相距 18.3mm），而零件本身仅约 19mm 宽，并集 bbox 19.17×10.97×3.21mm 正好等于整个零件——**撑大它的第二块只有 0.76mm³，占该差异体积 16%**。用户反馈"两处变动被一起圈起来，圈失去意义"根因即此。
**修法**：`split_clusters()` 拆 `removed`/`added` 的 Solids → 逐簇 `project_clusters()` 投影 → `merge_rects()` 合并重叠 → `draw_rects_on_image()` 逐框描边并在框外标圈号（引出线标注，见 H7）。修复后 PART-A 两框面积之和只占并集的 **0.231**。

**H2. 拆簇必须在预算阶段做（`.Solids` 是几何读取，受 A1 约束）。** 挪进渲染循环会重现 A1：`App::Part` 容器的 `.Shape` 被可见性写操作摘掉后，`.Solids` 一并取不到，簇数静默变 0、退回单框且不报错。

**H3. 3D 分离的簇在 2D 上可能重叠，必须投影后再合并，且每张图各算一次。**
PART-C 两簇中心仅相距约 2mm，特写图上投影必然重叠，画两个互相穿插的框比一个框更难看（实测该零件合并后=1 框，是正确结果，不是分簇失效）。而重叠关系取决于相机，整体图与特写图相机不同，所以**不能算一次到处复用**。唯一例外是旧版特写图——它必须复用新版特写的 rect 列表（坑 C2：两图靠同相机 + 同坐标实现像素级对齐）。

**H4. `boxZoom` 取景与 `score_direction` 评分仍用并集 bbox，不要顺手改成多框。**
取景换成单簇会让新旧特写覆盖区域不一致、破坏 C2 的对齐，并丢掉另一簇；评分换成多框会改变整体图选向结果，使实测基线全部对不上。`bbox_2d`/`bbox_2d_closeup` 字段因此保留并集语义。

**H5. 布尔碎片要过滤，但上限截断必须打印出来。** `CLUSTER_MIN_VOLUME=0.001`（与 `geom_diff.VOLUME_THRESHOLD` 同值）过滤近零碎片；`MAX_RECTS=8` 限制框数，超限时按面积保留前 N 并 `print` 被丢弃的簇号——静默截断会让"框覆盖完整"变成假象。

**H6. 单框图不标圈号。** 图上一个孤零零的"①"没有信息量；PPT 左栏同步：单簇直接给尺寸，多簇才加"共 N 处"与圈号。两侧的圈号字符序列必须一致（`render_diff.CIRCLED_DIGITS` 与 `build_pptx.CIRCLED`），否则读者无法把清单对应到框。

**H7. 圈号必须标在框【外】，用引出线连回框边，且落点要实测选。**
初版把红底白字标签画在框左上角**内侧**，框小时标签几乎填满整个框、把差异本身盖住（用户反馈）。现在改为标签放框外 + 一根细引出线连回框边锚点（`_place_label` / `_leader_cost`）。三条实测结论：
- **落点必须逐图实测，不能固定放某个角**：框外哪一侧是空白完全取决于该视角下模型的形状与位置。做法是在框外八个候选位采样非白像素占比。
- **评分必须同时算"落点空白度"与"引出线穿越代价"**。只看落点时实测会选中图片边缘的空角，引出线因此长距离横穿零件本体，比标签贴在框边更难看；只看路径又会把标签压在内容上。分数取 `空白度 − 穿越代价`。
- **标签用白底红字，不用红底白字**：落在图外空白区时实心红块比细边框远为抢眼，会把视线从品红高亮上引开。

**H8. 标签必须等所有框描完后单独一轮画。** 与框在同一个循环里画时，先画的标签会被后续候选位的空白度评估当成"内容"（而先画的框又影响不到已定好的落点），两轮分开才能让评估看到完整的框布局。

### I. 通用化改造（P0，2026-08-27）

**I1. 面数闸门拦不住布尔挂死，必须用子进程硬超时。**
实测 `PART-B` 仅 **912 面**（远低于 `MAX_FACES_FOR_BOOLEAN=5000`）却单次 `cut` 跑过 **840s 未返回**——面数与布尔耗时不成正比。此前靠 `SKIP_BASE_NAMES` 硬编码零件名规避，换装配体即失效，症状是**整条流水线无限期卡住、无输出无报错**。详见 §2 的子进程说明。面数闸门保留，但只作廉价的事前拦截（实测能挡住 8528 面的顶层装配体伪候选——该面数为 Windows 端实测，Linux 端同零件为 8553 面，见 §4——那个此前是被静默丢弃的）。

**I2. 位移类差异（`moved`）的高亮体与零件本体完全重合，必须隐藏零件本体。**
`moved` 没有对称差几何体，高亮体就是零件自身的 Shape。若零件也可见（`TARGET_TRANSPARENCY` 半透明），叠色会把品红冲淡到低饱和区（g 落在 60~110），于是 `score_direction` 的 `sat_in` 恒为 0、**每次都误判 fallback**，产出"其实看得很清楚却标注成被遮挡"的图。**指纹是 `sat_in == 0` 而 `magenta_px_overview` 很大**（实测 0 vs 17287），两个数字矛盾即此病。修法：`render_moved_one` 在整体图与新位置图里都把 `self_names` 子树设不可见，只留高亮体。`shape_changed` 不受影响——那里的高亮体是薄片状对称差，不与零件表面重合。

**I3. `moved` 的新旧两图必须各用自己的 bbox 画框。**
两图共用新位置 bbox 时，旧位置图的红框会画在品红零件**旁边**（PowerPoint 导出图目视发现，坑 G1 的又一次印证：坐标算得对不代表画面对）。`geom_diff` 因此为 `moved` 同时记 `bbox`（新）与 `bbox_old`（旧）。注意两图仍共用同一相机、投影在同一批完成，**不得**为此插入任何相机操作（坑 C2）。整体图同理要同时框住旧位置与新位置，否则读者看不出"从哪挪来"。

**I4. 整体配准检查不能用"两版全部几何的整体 bbox"。**
那样会把"增删零件"误判成"整体坐标系平移"：实测本案例基线（已知配准良好）被判 misaligned（中心位移 1.24mm、整体尺寸差 5.41%），根因是新版多了一个匿名对象 `SOLID003`（X[-10.15, 10.15]，超出旧版范围）把 bbox 撑大了。正确判据是**已配对零件的位移向量既够大又方向一致**：整体坐标系变了 → 全部零件同向同量；只改了几个零件 → 位移零散、多为 0。实测三档对照：基线 61 对（中位数 0mm）→ 不告警；合成整体平移 5mm → 告警；只有 2 个零件各挪 5mm、其余 38 个不动 → 不告警（一致性 0.707 < 0.8）。

### J. 真实公开语料实测（2026-10-04）

首次用**公开真实语料**（GitHub `XRobots/openDogV3` 的 8 实体零件机械装配体）做受控真值验证，暴露两个合成样例测不出的坑。

**J1. 名字折叠会把「同前缀的不同零件」并成一个候选，进而被容器判定整组吞掉——真值 1 处位移被报成「无差异」、退出码 0。**
零件名形如 `openDog V3_internals_toleranced v001/v002/.../v11`（FreeCAD/OCCT 导入时生成），`base_name` 把 8 个**不同零件**全折叠成同一个 key。几何层的容器判定是「子对象 key 命中候选集合即判为容器」，折叠后容器的子对象 key **等于容器自己的 key** → **自我命中** → 整组连同容器一起记入 `skipped_parts`，8 个零件无一参与比对。这是 CI 闸门最危险的假阴性：闸门放行了本该拦下的改动。
修法（两层同时改，缺一不可）：① `bom_diff.align_keys` 按**族**决定折叠与否——该族两版名字集合完全相同就用精确名（名字稳定时零歧义），否则才折叠（保留 Creo 实例编号漂移场景）；key 写进 `bom_diff.json` 的 `old_key_map`/`new_key_map`，几何层查这张表，**消除两份 `base_name` 真相源**。② 容器判定加 `k != bn` 防御，防止折叠族里再次自我命中。
**别再试这条错路**：只把容器判定改成按对象类型（`App::Part` + 非空 `Group`）判断不够——折叠让容器与零件共享同一 key，组级判定无法拆分，实测会让容器重新参与比对并报出重复计数（真值 1 处报成 2 处，D-015 回归）。

**J2. OCCT 布尔在「两个几乎完全重合的复杂 STEP 形状」之间会静默返回退化结果。**
实测同一零件的两个版本（28 面，体积差 10.4mm³，切掉 50% 体积也一样）：`common` 返回 **-265.07**（负体积）、`fuse` 得空形状且 bbox 为 **±DBL_MAX**、`cut` 结果比原形状还大（21188 > 20923）。同两个零件的形状本身没问题（`common(self,self)` 正常、与 0.5mm 平移副本布尔正常、与不相干零件布尔正常）——**只在近重合档退化**。合成样例（`Part.makeBox` 原生构造）不受影响，所以此前一直没暴露。
**当前处置（已实施）**：分两层。
① **诚实性**：`boolean_worker.py` 检出退化结果（`diff.isNull()` 或 bbox 三边长非有限）即返回 `ok:false, error:"boolean_no_result"`，父进程记入 `skipped_parts` 并打印——不再把「算不出来」静默当成「无差异」。
② **降级判定（不丢差异）**：`geom_diff` 在布尔失败但**体积差已超容差**时，照报 `shape_changed`，带 `highlight_mode:"whole_part"` 与 `degraded_reason`；`render_diff.precompute_shapes` 据此把**两版零件本体**当作 `removed`/`added`（语义成立：旧版整件在新版不再原样存在、新版整件是"新的形状"），于是走 `render_one` 的正常路径，高亮**整个零件**。`degraded_reason` 一路传到 `summary.parts_with_diff` 与报告（`report.field.highlight` = "whole part — …"）。
why 只在体积差超容差时降级：纯 bbox 变化（如旋转）没有体积证据，那种情况仍按算不出来记录，**不猜**。代价是 `removed_volume`/`added_volume` 在降级条目里等于整件体积而非真实增减料——降级标记就是为让读者知道这一点而存在的。
⚠️ 判据必须用 **`XLength`**（= XMax−XMin = inf），不能只判 `XMin`/`XMax` 是否 finite：退化 bbox 的两端是 ±DBL_MAX，是**有限值**，只判端点拦不住（已踩：第一版修复实测仍然静默通过）。

**I5. 子串断言要防"更长的数字"假失败。**
验证脚本里查 `"0.00 mm³" not in text` 来确认"没有误导性的零增减料行"，会被 `800.00 mm³`（零件体积）命中而假失败（本轮实测踩到）。改为精确匹配字段标签（`"减少材料" not in text`）。同类：任何对数字做子串匹配的断言都要想一遍"有没有更长的数会含它"。

---

## 6. 排查方法论（比结论更值得复用）

这类"代码不报错、产物是错的"问题，靠读代码和目视截图都定位不了。本项目验证有效的手段：

1. **实测像素优先，绝不靠推断下结论。** 统计截图里的实际品红像素范围，与代码算出的投影坐标逐一比对。多个坑表面症状相同（"框/高亮位置不对"），只有量化比对才能分开定位。
2. **诊断脚本必须复现完整上下文，否则结论不可信。** 本项目吃过两次亏：用"只测第 3 轮"的孤立脚本得出"一切正常"的错误结论——它跳过了前两轮的可见性写操作，恰好绕开了 A1；用只有 `fitAll` 取景的脚本验证坐标系，得出"y 轴没翻转"的错误结论——`fitAll` 居中取景下翻转前后数值几乎一样，必须用 `boxZoom` 偏心取景才能暴露 B1。**"孤立测试能过、完整流程不过"本身就是强信号**：问题在前序步骤的副作用，不在被测代码本身。
3. **开关法判定像素归属。** 隐藏/显示单个高亮体，对前后两张图做像素级 diff，能直接确定某簇像素属于谁。比"看着像是残留"可靠得多。
4. **多档对照区分错误类型。** 同一状态在多个取景档位各测一次：溢出量随缩放**成比例变化**→坐标系缩放问题；**恒定偏移**→原点问题；**只在单侧**→原点翻转。这套判别法直接定位了 B1。
5. **区分真高亮与混色赝像。** 真高亮体是全饱和品红 `(203,17,215)`/`(219,17,218)`；半透明零件透出的混色是低饱和紫 `(182,112,205)`。后者会被 LANCZOS 放大推过颜色阈值，凭空多出一簇"框外品红"。**判定像素归属应在原生渲染图上做，不要在放大后的图上做。**
6. **把自检数落进产物。** `render_manifest.json` 记录每张图的 `magenta_px_*`，异常一眼可见，不必每次重写诊断脚本。A1 和 C1 都是"不报错的静默失败"，靠人工看图漏了整整一轮。

---

## 7. 已知限制（PoC 阶段的既定取舍，不是 bug）

- `PART-B` 的布尔运算跑不出来：实测该零件**仅 912 面**，但单次 `cut` 超过 840s 未返回（面数与耗时不成正比）。现在它会在 `BOOLEAN_TIMEOUT_S`（默认 60s）被子进程硬超时中断、记入 `skipped_parts` 的 `reason:"timeout"` 并在产出物（报告）里显式列出，**不再挂死也不再静默跳过**；但该零件的几何差异仍无法检出。想检出需调大 `--boolean-timeout` 并接受相应耗时。
- 顶层装配体 `ASSY-TOP_1_1_ASM_1_A_ASM`（Windows 端实测 8528 面；Linux 端同零件为 8553 面）会作为伪候选出现。它不是真零件，跳过是正确行为——此前是被静默丢弃的，现在可见。
  **跳过它的判据是「装配体容器」而不是面数闸门**：STEP 导入把顶层装配体建成 `App::Part` 容器，而容器的"形状"是全部子件的并集——任何子件变化都会让容器**再报一次差异**（实测：3 零件合成样例因此被报成 3 处，真值 2 处）。现在的规则是：容器的子对象里有本次参与比对的候选，就记入 `skipped_parts` 的 `reason:"assembly_container"` 并跳过；**没有子件的空容器仍按普通零件处理**，否则会静默漏掉真实零件。面数闸门（`--max-faces`，默认 5000）依然保留，用来挡住那些**不是容器**却面数巨大的伪候选。
- **顶层装配体的产品名参与 BOM 比对**：STEP 导出会把文档名写进顶层 `PRODUCT` 记录，而 BOM 层按 `PRODUCT` 名比对，所以「把装配体改名」会被如实报成 added + removed 各一条。对真实 CAD 导出（顶层产品名是装配体名、跨版本稳定）通常无影响，但改名就是改名，工具不隐瞒。
- `PART-E` 未能匹配到几何对象：**真正原因是 FreeCAD 把无名 shape 导入成了匿名 Label**（`COMPOUND`/`COMPOUND001`，父级 `COMPOUND002`），与命名习惯无关，故 `base_name` 匹配不到。P1 的 `resolve_geometry_object`（按匿名对象的父级 Label 回溯）会修掉这条；当前仍以 `note: "no geometry match"` 显式报告，不静默漏掉。
- 遮挡判定用 bbox 粗判（非三角网格 raycast），单个差异簇的红框仍比其实际 2D 轮廓宽松（等轴视角下 3D bbox 投影天然比可见轮廓大）。逐簇标框（H1）解决的是"多处差异被一个大框套住"，不改变单框自身的这个宽松度。
- 渲染依赖 FreeCAD 完整 GUI 模式（`showMainWindow()`），运行时会短暂弹窗，不是完全无头。`setupWithoutGUI()` 试过，无法创建 ActiveDocument/ActiveView，不可用。
- **完全藏在装配体内部的差异，整体图仍看不到高亮**（实测 PART-C：即使非目标零件设 85 透明度，26 个方向里能看到它的只有一个正交侧视，而那张图画面糊成线框团）。此时代码走 fallback、选画面最清楚的方向、纯靠红框定位，并在报告与 PPT 文字带里说明原因。这是"看得懂的定位图"与"有高亮但看不懂的图"之间的取舍，不是 bug。
- 纯减料的零件，新版特写图上没有品红（材料只在旧版存在）。若人工觉得这种情况不够直观，下一步可考虑在新版图上叠一层半透明的"原材料位置"示意。
- 视角扫描使整条流水线从约 210s 增至约 355s（渲染 197s：每个差异多扫 26 个方向、每方向约 0.85s；几何 155s：其中 62s 是 PART-B 的布尔超时等待）。
- `moved` 类差异只报位移量与旋转角，**不做"沿哪条路径移动"的判定**，也不识别"零件被换成同体积的另一个零件"这种情形（体积与 bbox 都对得上时会被判 moved）。判据刻意从严，误判会落到 `shape_changed` 这个安全侧。
- `moved` 的旋转角优先用 `Shape.Placement.Rotation`；取不到时退化为"bbox 三边长多重集相同但排列不同"，那是**充分不必要**判据（对非 90° 整数倍的旋转不敏感），此时产物里标 `rotation_detected_by: "bbox_permutation"`，PPT 上注明"置信度较低"。
- **已知缺口**：`rotation_detected_by` 的置信度说明目前**只在 PPT 里**——`build_pptx.py` 读该字段并出括注「（由 bbox 排列推断，置信度较低）」（i18n 键 `pptx.value.rotation_bbox_note`），而 `report.py` **不渲染该字段**，报告读者只看到旋转角数值，看不到这条限定。补齐需在 `report.py` 里读它，并按 i18n 铁律同时补 en / zh 两份文案。
- 圈号引出线的落点在框外八个候选位里选优，未做全局布局优化。多框（>3）且彼此靠近时，后放的标签只做"不与已放标签重叠"的避让，不回溯重排前面的，因此可能出现引出线偏长的个例。实测本装配体最多 2 框，未出现该情况。

---

## 8. 产物字段契约（`render_manifest.json` — `report.html` / `report.md` / `compare.pptx` 的消费契约，改任一侧要同步）

```
base_name, instance_index,
change_type,                                   # "shape_changed" | "moved"，下游必须先判它
label_old, label_new,                          # 版本标签（图上标记带与下游（报告 / PPT）共用）
overview, overview_rect, closeup_new, closeup_new_rect, closeup_old, closeup_old_rect,
bbox_2d, bbox_2d_closeup,                      # 全部差异簇的【并集】框，仍供取景与溢出类断言用
rects_overview, rects_closeup,                 # 逐簇框（合并重叠后），[{rect:[x1,y1,x2,y2], labels:[1,2]}]
                                               # 坐标是放大后图片的像素坐标、已含 PADDING_PX
cluster_count, cluster_details, rect_line_width_px,
old_volume, new_volume, volume_delta, volume_delta_pct, diff_bbox_size_mm,
removed_volume, added_volume,
magenta_px_overview, magenta_px_closeup_new, magenta_px_closeup_old,
overview_view_tag, overview_view_dir, overview_view_score, overview_view_fallback,
overview_view_readability, overview_ecc, overview_compose, overview_body_pct,
overview_sat_in, overview_sat_out, overview_axis_dirs,
closeup_view_dir, closeup_axis_dirs, banner_h

仅 change_type == "moved" 的记录才有：
translation_mm, rotation_deg, rotation_detected_by, bbox_2d_closeup_old
```

`cluster_details` 每项：`{index, role("removed"/"added"), volume, size_mm}`，按体积降序、`index` 从 1 起，与图上红框圈号及报告左栏清单同序。

**`moved` 记录的既有字段取值约定**（避免下游把 0 误读成"没差异"）：`cluster_details=[]`、`cluster_count=0`、`removed_volume=added_volume=0`、`volume_delta=volume_delta_pct=0`。下游**必须**先判 `change_type` 再读这些字段——无条件读会把"只是挪了位置"渲染成"没有任何改动"。同理下游（报告 / PPT）在 `moved` 档都不显示增/减料行，PPT 的图例文案也不同（品红在那类图上代表零件本体而非改动的材料）。

空 manifest（`[]`）不是失败：`diff_count=0` 是有效结论，此时只出"未检出几何差异"说明页并以 **exit 0** 结束；被跳过/未匹配/被阈值筛掉的零件清单取自 `summary.skipped_parts` / `summary.unresolved_notes` / `summary.filtered_by_threshold`。manifest **文件缺失**才是失败，按对外契约记 **exit 2**（执行失败）。

---

## 9. CLI 接口边界（`caddiff diff` / `caddiff difftool`，调用方必读）

`caddiff diff` 是主入口：两个 STP 进去，`<out>` 下产出 `diff_manifest.json`（机器契约，含 `summary`）+ `report.html` / `report.md`（人读报告）+ `images/*.png`（差异图），中间产物 `bom_diff.json` / `geom_diff.json` 保留，`--pptx` 时另有 `compare.pptx`。另有 git 集成入口 `caddiff difftool`（选项与 `diff` 相同，只是 `-o` 默认落到新建的临时目录并打印报告路径）：**它检出差异也返回 0，只有执行失败才返回 2**——`git difftool` 把工具的非零退出码当成「external diff died」并中断整个 diff，所以检出差异必返 1 的 `diff` 不能给 git 用；反过来 **`difftool` 不能当 CI 闸门**（它永远不会因检出差异而返回非零），CI 里一律用 `caddiff diff`。git 侧配置与用法见 [`git-integration.md`](git-integration.md)。

**调用契约**
```bash
caddiff diff <OLD.stp> <NEW.stp> [选项]

# 位置参数
  OLD.stp / NEW.stp     两个 STEP/STP 文件

# 选项
  -o, --out DIR         输出目录（默认 ./caddiff-out）
  --label-old NAME      旧版标签（默认取文件名）
  --label-new NAME      新版标签（默认取文件名）
  --lang {en,zh}        产出物语言，默认 en（英文优先）
  --pptx                额外导出对比 PPT（默认不导出）
  --max-faces N         超过该面数跳过布尔运算（默认 5000）
  --boolean-timeout S   单次布尔运算硬超时秒数（默认 60）
  --min-diff-pct P      差异体积须大于零件体积的百分比（默认 0，不启用）
  --skip-parts A,B      跳过指定零件（按 base_name 精确匹配）
  --version / --help
```

文件名是哈希时必须显式给 `--label-old` / `--label-new`，否则图上与报告里的版本标签无意义。

**退出码语义（对外契约，可直接当 CI 闸门，不要只看有没有异常）**
- `0` = 两版无差异。**包含"未检出几何差异"这一档**——那时报告只有一页说明页、`render_manifest.json` 为 `[]`。**调用方不得把它当失败**，那是有效结论。
- `1` = 检出差异。
- `2` = 执行失败（输入不可读 / 子进程超时或崩溃 / 内部错误）。⚠️ **任何内部子步骤失败都必须传导到 2**，不得被折叠成 0 或 1。

**判断"这次跑出了什么"该读哪个字段**

`diff_manifest.json` 的 `summary` 块是**为调用方准备的单一入口**，读它就够，不必再关联其他文件（它只汇总、不重算，字段全部取自上游产物，避免"摘要与明细不一致"）：
```
summary.diff_count                    差异处数；0 即未检出差异（有效结论，不是失败）
summary.by_change_type                {"shape_changed": N, "moved": M}
summary.parts_with_diff[]             每处：base_name + change_type；
                                      shape_changed 带体积/占比/增减料/簇数，
                                      moved 带 translation_mm/rotation_deg
summary.skipped_parts[]               未参与比对的零件 + reason
                                      (timeout / too_complex / no_shape / user_skipped)
summary.unresolved_notes[]            没比上的（如 no geometry match / count mismatch）
summary.filtered_by_threshold[]       被 --min-diff-pct 筛掉的
summary.global_alignment_warning      true = 两版整体配准疑似不一致，差异可能大量为伪差异
summary.total_candidates              比对候选零件数
summary.settings                      本次实际生效的闸门取值（便于复现与调参）
```

**中间的三个 `skipped/unresolved/filtered` 是"诚实性"字段：不回传给用户，就等于让读者把"没提到"理解成"没差异"。** 逐差异的图片路径与像素自检数仍在 `render_manifest.json`，需要贴图或做校验时才读它。

`build_summary()` 对产物缺失/损坏是安全的（返回 `diff_count=0` 的空结构而非抛异常），但**不能据此判断成败**——那要看退出码。

**运行环境约束**
- **单次运行约 355~366s**（实测：BOM 0s / 几何 155~167s / 渲染 197~198s / PPT 1s），必须做成**异步任务或长超时的 CI 步骤**，不要挂在同步请求上。耗时随差异数增长（渲染约 22s/处：26 个候选视角 × 0.85s），渲染超时按差异数估算而非写死。
- **渲染依赖 FreeCAD 完整 GUI 模式**（`showMainWindow()`），会短暂弹窗，需虚拟显示（Xvfb 之类）或接受弹窗。`setupWithoutGUI()` 试过不可用（无法创建 ActiveDocument/ActiveView）。
- **布尔运算会再 fork 一层子进程**（`boolean_worker.py`），容器里要允许创建子进程，并给 `tempfile` 可写目录（BREP 落盘用，实测单个零件最大约 2.5MB）。
- **容器镜像必须装 `x11-utils`（提供 `xdpyinfo`）**：`deploy/docker-entrypoint.sh` 用 `xvfb-run` 提供虚拟显示，而 `xvfb-run` 靠 `xdpyinfo` 轮询判断 X server 是否就绪——缺了它，Xvfb 起来了但 python **从未被启动**，症状是容器 CPU 0%、零输出、不退出（实测：第一次真实运行镜像即踩到，`docker logs` 全程 0 行、`ps` 里只有 `sh` 与 `Xvfb`）。`apt install xvfb` **不会**自动带进这个依赖，必须显式装。
- 中文字体：容器内若没有 CJK 字体，图上中文会变豆腐块。`FONT_CANDIDATES` 已含 Linux 常见路径（Noto CJK / wqy），全未命中时会打印显著警告——**部署时要把这条警告当失败信号**，不要忽略（否则产出的是"能看但没字"的图）。
- **并发未验证**：单进程单 GUI 的假设下写的，同一台机器并行跑多个实例是否互相干扰（ActiveView 抢占、临时目录冲突）**没有实测过**，先按串行队列部署。

---

## 10. 涉及文件

- `caddiff/bom_diff.py` — BOM 差异（系统 Python，纯文本解析 STEP 的 PRODUCT 实体）
- `caddiff/geom_diff.py` — 几何差异（FreeCAD Python，console 模式）。`classify_change()` 三态分类（identical/moved/shape_changed）；`placement_delta()` 算位移与旋转；`symmetric_diff_isolated()` 把布尔丢进子进程并硬超时（坑 I1）；`check_global_alignment()` 由已配对零件的位移一致性判整体配准（坑 I4）。产出含 `change_type`/`skipped_parts`/`filtered_by_threshold`/`global_alignment`/`settings`
- `caddiff/boolean_worker.py` — 子进程里做布尔对称差（FreeCAD Python）。**必须先 import FreeCAD 再 import Part**，否则 0xC0000005 崩溃（坑 I1）
- `caddiff/render_diff.py` — 渲染+红框+品红高亮+新旧并排+方向标记带（FreeCAD Python，GUI 模式）。**`run()` 三阶段顺序是硬约束**（坑 A1）；`capture_and_project()` 保证截图与投影同源（坑 B1/B2）；`pick_best_direction()`/`score_direction()` 是整体图自动选向（坑 F1~F4）；`add_direction_banner()` 画方向标记带（坑 F5/F6）；`split_clusters()`/`project_clusters()`/`merge_rects()`/`draw_rects_on_image()` 是逐差异簇分别标框（坑 H1~H6）；`_place_label()`/`_leader_cost()`/`_region_emptiness()` 是圈号的框外引出线标注与落点实测选位（坑 H7/H8）；`render_moved_one()`/`draw_move_arrow()` 是位移类差异的渲染（坑 I2/I3）；`derive_label()` 推导版本标签；`write_manifest()` 原子落盘。输出目录由调用方传入（`caddiff diff` 传 `<out>/images`）
- `caddiff/report.py` — 差异报告生成（系统 Python，纯标准库）：读 `diff_manifest.json` + 同目录 `images/render_manifest.json`（拿逐处差异的图片文件名与数值），出 `report.html` 与 `report.md`。**报告独立成模块、PPT 降为可选是决策 D-006**：HTML/Markdown 是默认产物，PR 里能直接贴 Markdown、浏览器里能直接看 HTML，而 PPT 要求读者装 PowerPoint。**刻意不引模板引擎**：报告是给人看的一次性产物，为它拉一个 Jinja2 依赖不划算，`html.escape` + f-string 足够。**不要读 `geom_diff.json` 重新推导结论**——那会让报告与 manifest 出现两套口径
- `caddiff/build_pptx.py` — PPT 拼版（系统 Python）：标题条 + 三图横排 + 两栏结构化信息带 + 图例（版式约束见坑 G1~G7）。`cluster_size_lines()` 按簇列尺寸，圈号 `CIRCLED` 必须与 render_diff 一致（坑 H6）；按 `change_type` 分派 shape_changed / moved 两套版式；`build_empty_slide()` 出"未检出差异"说明页（含被跳过零件清单）；`--pptx` 时才被调用
- `caddiff/run_pipeline.py` — 编排入口（`caddiff diff` 子命令的流水线实现），注意步骤 3 要传 `stp_old`+`stp_new` 两个路径。异常与超时会打印已产出的中间产物路径；渲染超时按差异数估算而非写死；`build_summary()` 把散落在多个 JSON 里的结论汇总进 `diff_manifest.json` 的 `summary` 块，供调用方单点消费（见 §9）
- `caddiff/cli.py` — 命令行入口（argparse）与**退出码契约的唯一实现处**：编排层失败一律抛 `PipelineError`，由这里翻译成 2，与"检出差异 = 1"严格分开（早先任何一步失败都 `sys.exit(1)`，于是"FreeCAD 崩了"和"两版真有差异"在 CI 里长得一模一样，闸门会放行本该拦下的提交、也会拦住本该放行的提交）；argparse 自身报错也是 2，与本契约一致
- `caddiff/version.py` — 版本号**唯一出处**（`caddiff --version` 从这里读，`pyproject.toml` 用 `dynamic = ["version"]` + `version = { attr = "caddiff.version.__version__" }` 也从这里读）。**不要在别的文件里再写一遍版本号**——两处写必然漂移，而版本号漂移会让「镜像 tag ↔ 源码 tag ↔ 构建脚本一一对应」这条合规要求失效（AGENTS.md §6）
- `caddiff/__init__.py` — 包入口。其 docstring 记录了**扁平 import 是刻意的**（`import fcenv` 而不是 `from . import fcenv`）：几何/渲染模块必须由 **FreeCAD 自带的解释器**以「独立脚本」方式启动，那个解释器里没有安装本包，相对 import 会直接失败；入口（`cli.py`）负责把包目录塞进 `sys.path`，让扁平 import 在「源码运行」与「pip 安装」两种形态下都成立
- `caddiff/make_moved_fixture.py` — 生成 `moved` 类的合成 STP 用例（FreeCAD Python）。真实 STP 里没有位移类差异，这是该分支唯一的端到端验证途径
- `caddiff/fcenv.py` — FreeCAD 解释器/库路径解析的**唯一真相源**（`FREECAD_PYTHON` → `FREECAD_HOME` → Linux 常见安装路径 → `PATH`；找不到就抛错并给修复指引，**不静默回退到当前解释器**——静默回退会在 `import FreeCAD` 处才炸，报错点离真正原因很远）
- `caddiff/console.py` — 控制台编码兜底：FreeCAD 自带的 Python 3.11 在中文 Windows 上 stdout 是 GBK，`mm³` 这类字符会让 `print` 抛 UnicodeEncodeError 把脚本打成退出码 1（实测：STP 已正确写出后仍因最后一行 print 崩掉）。tty 保留本机编码、只把不可编码字符降级为 `?`；管道/重定向改 UTF-8
- `caddiff/i18n.py` — 产出物文案的唯一入口 `t(key, **kw)`（默认 `en`，`--lang zh` 或环境变量 `CADDIFF_LANG`；键缺失时回退默认语言、再缺失就原样返回 key，缺翻译会显式暴露在产出物里）。**控制台进度输出不进这张表**（一律英文），locale-neutral 字符与 `skipped_parts.reason` 枚举值永不翻译
- `docs/pipeline.md` — 本文件：diff 能力线的模块功能文档（只写「为什么」与「不写就会踩的坑」）
- 仓库级说明性文件：[`../README.md`](../README.md)（对外用法与首屏）、[`../AGENTS.md`](../AGENTS.md)（项目宪法：命名/契约铁律/决策记录）
