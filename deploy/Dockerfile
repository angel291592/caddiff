# syntax=docker/dockerfile:1
#
# caddiff —— 单机版镜像（对外分发形态）
#
# 构建：
#   docker build -f deploy/Dockerfile -t caddiff .
# 运行（一条命令，卷挂当前目录）：
#   docker run --rm -v "$PWD:/data" caddiff diff /data/old.stp /data/new.stp -o /data/report
#
# ── 合规要点（改本文件前必读 AGENTS.md §6）────────────────────────────────
# 1. 本镜像**打包了 FreeCAD / OpenCASCADE（LGPL，且 FreeCAD 发行版内还含 GPL 文件）**。
#    因此镜像 tag 必须与源码 tag、本构建脚本一一对应，README 也必须说明
#    「仓库代码 Apache-2.0」与「镜像内第三方组件各自许可证」是两套东西。
# 2. **绝不把 GPL/LGPL 二进制提交进 git**——只提交本 Dockerfile，二进制一律构建期 apt 装。
# 3. 与 FreeCAD 的交互方式必须保持 **subprocess + 文件交换**（见 caddiff/geom_diff.py
#    拉起 caddiff/boolean_worker.py 的方式）。禁止改成编译扩展 / dlopen / 共享内存。
# 4. 上游许可证文本留在镜像内（Debian 的 /usr/share/doc/*/copyright），不要删。
#
# ── 版本固定 ─────────────────────────────────────────────────────────────
# FreeCAD 来自 PPA，apt 的版本串随发布浮动，写死一个精确版本号会让构建在某天突然
# 找不到包。这里改用「**期望版本 + 构建期硬断言**」：版本不符就直接构建失败，
# 而不是悄悄产出一个与文档不符的镜像。
ARG FREECAD_EXPECT_MAJOR_MINOR=1.0

FROM ubuntu:24.04

ARG FREECAD_EXPECT_MAJOR_MINOR
ARG DEBIAN_FRONTEND=noninteractive

ENV LANG=C.UTF-8 \
    LC_ALL=C.UTF-8 \
    PYTHONUNBUFFERED=1 \
    LIBGL_ALWAYS_SOFTWARE=1 \
    FREECAD_PYTHON=/usr/lib/freecad/bin/freecad-python3

# ── 系统依赖 ─────────────────────────────────────────────────────────────
# freecad            几何内核 + FreeCAD 模块（LGPL / 部分 GPL，见 THIRD_PARTY_LICENSES.md）
# xvfb               无显示环境下提供虚拟 X display —— 渲染必须要有真实 GUI 视口
# x11-utils          **xvfb-run 的硬依赖**：它靠 `xdpyinfo` 轮询判断 X server 是否就绪，
#                    缺了它 xvfb-run 会永远卡在等待循环里——Xvfb 起来了、python 却从未被
#                    启动，容器 CPU 0%、零输出、不退出（实测第一次真实运行镜像即踩到）。
#                    这条依赖不会在 `apt install xvfb` 时自动带进来。
# libgl1-mesa-dri / libglu1-mesa   软件渲染（容器里没有 GPU）
# fonts-noto-cjk     中文产出物需要；缺失时图上会出豆腐块且**不报错**
# python3-numpy / python3-pil      渲染打分与画框用；必须装进 FreeCAD 解释器能看到的路径
RUN apt-get update \
    && apt-get install -y --no-install-recommends software-properties-common gnupg ca-certificates \
    && add-apt-repository -y ppa:freecad-maintainers/freecad-stable \
    && apt-get update \
    && apt-get install -y --no-install-recommends \
        freecad \
        xvfb \
        x11-utils \
        libgl1-mesa-dri \
        libglu1-mesa \
        fonts-noto-cjk \
        python3 \
        python3-numpy \
        python3-pil \
        python3-pip \
        curl \
    && rm -rf /var/lib/apt/lists/*

# ── 构建期断言：FreeCAD 版本必须与声明的期望一致 ──────────────────────────
RUN set -eux; \
    ver="$($FREECAD_PYTHON -c 'import FreeCAD; print(".".join(FreeCAD.Version()[:2]))')"; \
    echo "FreeCAD detected: $ver (expected $FREECAD_EXPECT_MAJOR_MINOR)"; \
    if [ "$ver" != "$FREECAD_EXPECT_MAJOR_MINOR" ]; then \
        echo "ERROR: FreeCAD version drift — update FREECAD_EXPECT_MAJOR_MINOR and the docs together." >&2; \
        exit 1; \
    fi

# ── 构建期断言：几何与渲染依赖在 **FreeCAD 解释器** 里必须可导入 ──────────
# 这两个包若只在系统 python 里，渲染步骤会以 ModuleNotFoundError 失败；
# 早先的镜像没有显式安装 numpy，属于「碰巧能用」，这里改成硬断言。
RUN $FREECAD_PYTHON -c "import FreeCAD, Part, FreeCADGui, numpy, PIL; \
    print('freecad-python OK:', FreeCAD.Version()[:3], 'numpy', numpy.__version__, 'PIL', PIL.__version__)"

# ── 可选依赖：PPT 导出（决策 D-006，--pptx 才用到）────────────────────────
RUN pip3 install --no-cache-dir --break-system-packages "python-pptx>=0.6.23"

# ── 中文字体命中检查（缺失时图上出豆腐块且不报错，必须在构建期拦下）──────
RUN test -f /usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc \
    || (echo "ERROR: CJK font missing — zh output would render tofu boxes." >&2; exit 1)

# ── 应用代码 ─────────────────────────────────────────────────────────────
# 只拷运行需要的目录：文档、测试、示例不进镜像（镜像越小，冷启动越快——发布闸门②）
WORKDIR /app
COPY caddiff/ /app/caddiff/
COPY deploy/docker-entrypoint.sh /usr/local/bin/caddiff
RUN chmod +x /usr/local/bin/caddiff

# ── 构建期冒烟：CLI 必须真的能跑起来 ──────────────────────────────────────
RUN caddiff --version && caddiff --help > /dev/null

# 用户的 CAD 文件挂到这里；`-v "$PWD:/data"` 之后直接写 /data/... 即可
WORKDIR /data

ENTRYPOINT ["/usr/local/bin/caddiff"]
CMD ["--help"]
