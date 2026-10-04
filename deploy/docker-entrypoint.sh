#!/bin/sh
# caddiff 容器入口：在没有 DISPLAY 的环境里起一个 Xvfb 虚拟显示再跑。
#
# why 必须要 Xvfb：render_diff.py 走 FreeCAD 的 **GUI** 路径（要 ActiveView 才能截图、
# 才能扫 26 个候选视角）。纯 headless 的 FreeCADCmd 没有 3D 视口，渲染步骤直接不可用。
# 这不是可以省掉的一层——省掉它，`docker run` 会在渲染步骤崩掉。
#
# why 不设 QT_QPA_PLATFORM=offscreen：offscreen 平台下 FreeCAD 的 3D 视口拿不到
# 真实窗口尺寸，截图会是空白。虚拟 X display 才是可用的路径。
set -eu

if [ -z "${DISPLAY:-}" ]; then
    exec xvfb-run -a --server-args="-screen 0 1600x1200x24" \
        python3 /app/caddiff/cli.py "$@"
fi

exec python3 /app/caddiff/cli.py "$@"
