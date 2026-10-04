#!/bin/sh
# caddiff 容器入口：在没有 DISPLAY 的环境里起一个 Xvfb 虚拟显示再跑。
#
# why 必须要 Xvfb：render_diff.py 走 FreeCAD 的 **GUI** 路径（要 ActiveView 才能截图、
# 才能扫 26 个候选视角）。纯 headless 的 FreeCADCmd 没有 3D 视口，渲染步骤直接不可用。
# 这不是可以省掉的一层——省掉它，`docker run` 会在渲染步骤崩掉。
#
# why 不设 QT_QPA_PLATFORM=offscreen：offscreen 平台下 FreeCAD 的 3D 视口拿不到
# 真实窗口尺寸，截图会是空白。虚拟 X display 才是可用的路径。
#
# ⚠️ why 这里**不能**写 `exec xvfb-run`（实测 A/B 对照，只差这一个词）：
#     `exec` 会让 xvfb-run 变成容器的 **PID 1**，于是它挂死——Xvfb 起来了、子进程 python3
#     从未被启动、容器 CPU 0%、日志全空、永不退出。同一命令去掉 `exec`（shell 留在 PID 1，
#     xvfb-run 当子进程）立刻成功退出。
#     机制（**推断**，未逐行验证）：xvfb-run 靠 `trap : USR1` + `wait` 等 Xvfb 报就绪，而
#     `trap` 是在 fork 之后才装的；PID 1 对内核对「默认动作为终止」的信号是**直接丢弃**，
#     就绪信号若落在装 trap 之前就永远丢了，`wait` 便无限等下去。非 PID 1 时同样的竞态会
#     让 xvfb-run 直接死掉——所以这个坑只在容器入口处出现，特别隐蔽。
#     保留一个 shell 当 PID 1 还顺带负责回收子进程，这也是容器里通常推荐的形态。
set -eu

if [ -z "${DISPLAY:-}" ]; then
    # 不加 exec，见上方说明。脚本的退出码 = xvfb-run 的退出码 = cli.py 的退出码，
    # 所以 §3 的退出码契约（0/1/2）不受影响。
    xvfb-run -a --server-args="-screen 0 1600x1200x24" \
        python3 /app/caddiff/cli.py "$@"
    exit $?
fi

exec python3 /app/caddiff/cli.py "$@"
