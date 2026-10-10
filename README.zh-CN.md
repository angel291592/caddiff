<div align="center">

# caddiff

[English](https://github.com/angel291592/caddiff/blob/main/README.md) | 简体中文

**CAD 装配体的 git diff。**

两个 STEP 文件进，一张*改了什么*的图出 —— 外加一份你的 CI 可以拿来当闸门的差异清单。

[![CI](https://github.com/angel291592/caddiff/actions/workflows/ci.yml/badge.svg)](https://github.com/angel291592/caddiff/actions/workflows/ci.yml)
[![License](https://img.shields.io/badge/license-Apache--2.0-blue.svg)](https://github.com/angel291592/caddiff/blob/main/LICENSE)
[![Python](https://img.shields.io/badge/python-3.9%2B-blue.svg)](pyproject.toml)

</div>

![caddiff 高亮出一个被移动的零件和一个被删除的特征](https://raw.githubusercontent.com/angel291592/caddiff/main/docs/images/hero.png)

> 偏绿的品红色标出发生变化的几何；红框框出它的位置；蓝色箭头
> 表示 5.00 mm 的位移。两张图都由 `caddiff` 基于 [`examples/`](https://github.com/angel291592/caddiff/tree/main/examples) 里的
> 合成样例生成 —— 本仓库任何地方都没有客户数据。
> **[免安装，在线浏览完整样例报告 →](https://angel291592.github.io/caddiff/report.html)**

---

## 问题所在

你把 CAD 放进了 git。现在有人来问：*「v1.2 和 v1.3 之间到底改了什么？」*

`git diff` 在这里毫无用处。STEP 文件是纯文本，git 会打印出四千行坐标，
却什么都不告诉你。更糟的是，git 会欣然把两个 STEP 文件
**合并**成一个语法上站得住、几何上却是错的文件 —— 而且没有任何东西警告你。

商业 CAD 套件卖一个针对这个问题的方案（SOLIDWORKS Compare、TransMagic、3DViewStation）。
开源一侧几乎什么都没有：这个赛道里星数最高的项目停在
**75 stars**，而且只渲染一个可视化 diff —— 不做 BOM 对齐、没有报告、也没有面向 CI 的方案。

`caddiff` 补上的正是缺的那一块：一个命令行工具，用一张图和一份机器可读的清单回答
*改了什么*。

---

## 快速开始

### Docker（推荐 —— 无需安装任何东西）

```console
$ docker run --rm -v "$PWD:/data" ghcr.io/angel291592/caddiff:v0.3.3 \
    diff /data/old.stp /data/new.stp -o /data/report
```

`v0.3.3` 是已发布的镜像。另有一个 `edge` tag 跟着 `main`，供想
踩在最新代码上的人用 —— 但凡是你要给 CI 当闸门用的地方，请钉住一个已发布的 tag（或 digest）。

### 本地安装

`caddiff` 本身**零第三方依赖** —— 但几何与渲染两步跑在 FreeCAD 里，
所以 `FREECAD_PYTHON` 必须指向一个能 `import FreeCAD` 的解释器：

```console
$ pip install caddiff          # 想要 --pptx 就加上 [pptx]
$ export FREECAD_PYTHON=/path/to/FreeCAD/bin/python.exe   # Windows：FreeCAD 发行版自带 python
$ export FREECAD_PYTHON=/usr/bin/python3                  # Linux：系统 python3，
$ export PYTHONPATH=/usr/lib/freecad/lib                  #        外加 FreeCAD 模块所在目录
$ caddiff diff old.stp new.stp -o report
```

Linux 上**不要**把 `FREECAD_PYTHON` 指向 `.../freecad/bin/freecad-python3`：那个文件是
FreeCAD 的 **GUI 应用**，把脚本交给它会启动界面并**永不返回**。上面这套「系统 `python3` +
`PYTHONPATH`」才是能用的写法，Docker 镜像也是这么配的（见 [`docs/pipeline.md`](https://github.com/angel291592/caddiff/blob/main/docs/pipeline.md)）。

如果 `FREECAD_PYTHON` 未设置，`caddiff` 会搜索常见的 Linux 位置和 `PATH`，
找不到时会**带着可操作的提示失败**，而不是静默回退到一个
import 不了 FreeCAD 的 Python。

### 通过 `git difftool` 使用

让 `git diff for CAD assemblies` 字面上成立：

```console
$ git config --global difftool.caddiff.cmd 'caddiff difftool "$LOCAL" "$REMOTE"'
$ git difftool -t caddiff HEAD~1 -- bracket.stp
```

为什么 CAD 文件需要 `.gitattributes` 的专门处理、如何挑选要比对的版本，
见 [`docs/git-integration.zh-CN.md`](https://github.com/angel291592/caddiff/blob/main/docs/git-integration.zh-CN.md)。

### 作为 GitHub Action（PR 闸门）

本仓库内置一个现成的 Action：拉取同一个预构建镜像，
对你仓库里的两个文件跑一次 diff，并把报告目录作为 workflow artifact 上传：

```yaml
- uses: angel291592/caddiff@v0.3.3
  id: caddiff
  with:
    old: models/base.stp
    new: models/pr.stp
    image: ghcr.io/angel291592/caddiff:v0.3.3

- name: 响应差异
  if: steps.caddiff.outputs.has-differences == 'true'
  run: echo "::warning::${{ steps.caddiff.outputs.diff-count }} CAD change(s) detected"
```

输出项读取自 `diff_manifest.json`（`has-differences`、`diff-count`、`report-dir`）——
这个 Action 只在退出码 2（caddiff 未能运行）时失败；退出码 1（检出差异）是一个结果，
报告照常上传。

`image:` 是特意写出来的：`@v0.3.3` 解析到的是 `v0.3.3` tag 上的 `action.yml`，
而那个 tag 里的默认值被冻结在**上一个**版本 —— 不写这行，action 与几何内核就会跑在两个版本上。
规则与上面的 `docker run` 一致：钉镜像，不钉分支。

已知限制：来自 fork 的 PR 用默认只读 token 无法在 PR 上评论，
且报告以 artifact 而非 PR 评论的形式交付 ——
见 [`action.yml`](https://github.com/angel291592/caddiff/blob/main/action.yml) 顶部的说明。

---

## 它能检测什么

| 差异类型 | 例子 | 报告方式 |
|---|---|---|
| **零件新增 / 删除** | 装配体里去掉了一个支架 | BOM 级：列在 `summary.bom_added` / `bom_removed` |
| **实例数变化** | 2 颗螺丝变成了 4 颗 | BOM 级：列在 `summary.bom_count_mismatch` |
| **`moved`**（移动） | 同一零件，发生了平移或旋转 | 以 mm/° 报告位移量，图上画蓝色箭头 |
| **`shape_changed`**（形状变化） | 材料被加上或去掉了 | 布尔对称差，品红色高亮，并给出体积差 |

**无法被比对**的零件（面数太多、布尔超时、没有实体形状）绝不静默丢弃 —— 它们会连同
原因一起出现在 `summary.skipped_parts` 里。「没提到」
绝不允许被读成「没差异」。

---

## 你会得到什么

```
report/
├── diff_manifest.json     机器可读契约（CI 场景从这里读起）
├── report.html            面向人的报告，图片内嵌
├── report.md              同一份内容的 Markdown 版（适合贴进 PR 评论）
├── images/*.png           总览图 + 每处差异的新旧特写
├── compare.pptx           仅当加 --pptx 时才有
├── bom_diff.json          中间产物
└── geom_diff.json         中间产物
```

每处差异都会得到一个**自动选定的视角**：`caddiff` 扫描 26 个候选方向
（6 个面 + 12 条棱 + 8 个角），按有多少被高亮的几何可见、构图好不好读给每个方向打分，
然后选出得分最高者。当差异被埋在装配体内部时，它会在图上直说，
而不是装作这张图没问题。

---

## 退出码 —— 把它当 CI 闸门用

| 退出码 | 含义 |
|---|---|
| `0` | 无差异 |
| `1` | 检出差异 |
| `2` | 运行失败（输入有误、超时、崩溃） |

```yaml
- name: 检查几何差异
  run: |
    docker run --rm -v "$PWD:/data" ghcr.io/angel291592/caddiff:v0.3.3 \
      diff /data/base.stp /data/pr.stp -o /data/report
```

`1` 和 `2` 的区分很重要：FreeCAD 崩溃绝不能看起来像
「没有变化」。

---

## 与同类工具的对比

「CAD diff」这个词有两层含义，二者不是竞争关系 —— 它们是
同一个闭环的两半。`caddiff` 的位置在这里。

| | **caddiff** | text-to-CAD 工具<br><sub>[text-to-cad](https://github.com/earthtojake/text-to-cad), [CADAM](https://github.com/Adam-CAD/CADAM)</sub> | SOLIDWORKS Compare<br><sub>商业版本比对工具</sub> |
|---|---|---|---|
| **用途** | 核对两个版本之间改了什么 | 从 prompt 生成 / 编辑模型 | 核对版本 |
| 回答「这个模型改了没有、怎么改的？」 | ✅ 确定性几何比对 | ❌ 没有这种概念 | ✅ |
| 直接处理你手头就有的一对文件 | ✅ | ❌ 需要 prompt 并重新生成一遍 | ✅ |
| 机器可读的差异清单 | ✅ `diff_manifest.json` | ❌ | ❌ |
| 能给 PR 当闸门 | ✅ 退出码 `0/1/2` | ❌ | ❌ |
| 无界面 / 能跑在容器里 | ✅ | 视实现而定 | ❌ |
| 需要 CAD 许可证 | ❌ | ❌ | ✅ |
| 开源 | ✅ Apache-2.0 | ✅（视项目而定） | ❌ |

**互补，而非对手。** text-to-CAD 工具极其擅长*制造*改动，对*核对*
改动却无话可说。当越来越多的修改出自 agent 而非人手时，
这个缺口只会拉大：

```console
# 一个 agent（或你本人）刚重写了支架
$ caddiff diff old.stp new.stp -o report
$ echo $?
1        # 有改动 —— 具体是什么、改了多少，全在这里
```

如果你用 text-to-CAD 工具生成模型，那么**在它之后**该跑的就是 `caddiff`。

**与商业版本比对工具的对比。** 它们把同一个问题解决得不错，但要按席位付许可证费、
要求安装 CAD 应用、也无法在 CI 里跑。这正是本项目存在的全部理由 ——
见[问题所在](#问题所在)。

> 开源一侧，这个赛道里有几个项目能渲染可视化 diff。其中星数最高的停在 **75 stars**，
> 而且没有一个提供 BOM 级对齐、机器可读的 manifest 或 CI 退出码。
> 这正是 `caddiff` 填补的空缺。

---

## 性能基准（实测，非估算）

下列全部数字为墙钟耗时，软件渲染，无 GPU。渲染占大头：
每处差异的开销是一次 26 方向扫描（每个方向 ≈0.85 s）加三张图。

| 输入 | 运行环境 | 检出差异 | 墙钟耗时 |
|---|---|---|---|
| 3 零件合成装配体（[`examples/`](https://github.com/angel291592/caddiff/tree/main/examples)） | 源码检出，Windows + FreeCAD 1.1.3 | 2 | **≈80 s** |
| 3 零件合成装配体，同一样例 | 官方镜像，Linux + FreeCAD 0.21.2 | 2 | **≈35 s** |
| 55 零件生产装配体 | 源码检出，Windows + FreeCAD 1.1.3 | 3 | **290–366 s** |

镜像跑小样例更快，是因为容器是干净的 Linux 文件系统 —— 中间的 STEP/BREP 文件不会被
Windows Defender 扫描。不要把这读成 0.21 比 1.1.3 更快：
这两行之间相差不止一个变量。

自己动手复现第一行：

```console
$ python caddiff/make_moved_fixture.py examples/fixtures
$ caddiff diff examples/fixtures/moved_old.stp examples/fixtures/moved_new.stp -o out
```

我们不宣称什么速度纪录。如果你需要在超大装配体上做到亚秒级 diff，
这个工具现在还满足不了你 —— 见路线图。

---

## 它**不**做什么

把边界挑明，因为一个过度承诺的工具比没有工具更糟：

- **不做公差分析与 GD&T。** `shape_changed` 的含义是「布尔对称差非空」，
  不是「这个超出公差了」。
- **不做自由曲面偏差图。** 我们比对的是实体，不是点云。
- **不做超大装配体。** 渲染这一步是串行的；55 零件的装配体已经要花好几分钟。
  没有强制上限，但体验会随之变差。
- **零件配对是启发式的。** 同名零件按包围盒邻近度配对，并带 50 % 体积护栏。对
  「同一装配体、就地编辑」这种常见场景，它效果良好；当零件被整批改名或整个
  装配体被挪动时，它**已知会变弱**。未配对成功的零件照实上报，绝不靠猜。
  在 24 对真实公开装配体上实测：结构性变化全部被抓到；但 15 个结构未变的
  对子里，有 7 个仍会报出纯因**改名**（人工改名、
  导出器自动命名、甚至仅大小写之差）产生的成对 BOM 新增/删除 —— 名字确实变了，
  而几何无法跨名字配对，所以对称的 added/removed 清单应读作
  「有东西被改名了」。同名零件的变化一旦超出 50 % 配对护栏，就会被作为
  整件变化（whole-part change）上报（降级、明确标注），
  绝不丢弃。
- **不做合并。** `caddiff` 只告诉你改了什么。它不合并两个 STEP 文件，
  也不打算去试。
- **不做托管服务。** 一切都跑在本地或你自己的 CI 里。什么都不上传。

---

## 工作原理

```
old.stp ─┐
         ├─► [1] BOM 对齐             (纯 Python — 零件名、实例数)
new.stp ─┘        │
                  ├─► [2] 几何 diff          (FreeCAD: 分类 identical/moved/shape_changed,
                  │                           布尔对称差在可被强杀的子进程里算)
                  ├─► [3] 渲染               (FreeCAD GUI + 26 方向视角搜索 → PNG)
                  └─► [4] 报告               (manifest JSON + HTML + Markdown; PPTX 按需生成)
```

两条承重的设计规则：

- **每一项昂贵的几何运算都跑在带硬超时的子进程里。** 面数预测不了布尔运算的
  耗时 —— 我们实测过一个 912 面的零件，它单次 `cut` 就跑过了
  840 s。只靠面数闸门抓不住这种事；只有可被强杀的子进程才抓得住。
- **与 FreeCAD 打交道永远只走 subprocess + 文件。** 不编译扩展、不 `dlopen`、不共享内存。
  这让 LGPL/GPL 边界保持干净（见
  [`THIRD_PARTY_LICENSES.md`](https://github.com/angel291592/caddiff/blob/main/THIRD_PARTY_LICENSES.md)），也让崩溃被控制住。

技术细节深入讲解：[`docs/pipeline.zh-CN.md`](https://github.com/angel291592/caddiff/blob/main/docs/pipeline.zh-CN.md)。

---

## 路线图

- **v0.1 – v0.3**（已发布）—— `caddiff diff`：BOM 对齐、几何 diff、渲染、
  HTML/Markdown/JSON 报告、Docker 镜像、GitHub Action、CI 退出码。
- **下一步** —— 一个 MCP 服务器，让刚编辑完模型的 agent 能拿上一版
  来核对它自己的改动。
- **之后** —— `caddiff check`（FEA 合理性检查）与 `caddiff build`（沙箱化建模）。
  命名空间已保留；不作任何承诺。

[![Star History Chart](https://api.star-history.com/image?repos=angel291592/caddiff&type=Date)](https://star-history.com/#angel291592/caddiff&Date)

---

## 参与贡献

欢迎提交 bug 报告、样例对和 pull request —— 见
[`CONTRIBUTING.zh-CN.md`](https://github.com/angel291592/caddiff/blob/main/CONTRIBUTING.zh-CN.md)。请**绝不要**往 issue 里附真实的客户模型：
CAD 文件里常常装着商业机密，衍生的 PNG/JSON/HTML 同样带着
零件名。请改用最小的合成样例。

安全漏洞报告：[`SECURITY.zh-CN.md`](https://github.com/angel291592/caddiff/blob/main/SECURITY.zh-CN.md)。

---

## 许可证

仓库源码为 **Apache-2.0**（[`LICENSE`](https://github.com/angel291592/caddiff/blob/main/LICENSE)）。

Docker 镜像**以各自的许可证捆绑了第三方组件** —— FreeCAD
（LGPL-2.1-or-later，且其发行版中还包含 GPL 许可的文件）、OpenCASCADE
（LGPL-2.1，带 OCCT exception）、numpy、Pillow、python-pptx。这些*不*在
本仓库 Apache-2.0 许可证的覆盖范围内。获取对应源码等完整说明见
[`THIRD_PARTY_LICENSES.md`](https://github.com/angel291592/caddiff/blob/main/THIRD_PARTY_LICENSES.md)。

本项目与 FreeCAD 项目或 FreeCAD Project Association 无隶属关系，也未获其认可。
本项目使用 Open CASCADE Technology。
