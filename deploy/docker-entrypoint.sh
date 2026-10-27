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
#     机制（在镜像里实测 + X server 源码）：xvfb-run 靠 `trap : USR1` + `wait` 等 Xvfb 发
#     就绪信号，trap 在 fork Xvfb **之前**就装好了（不是之后）；而 X server 只在「父进程
#     PID > 1」时才发这个信号（源码 `RunFromSmartParent` 分支带 `ParentProcess > 1` 守卫）。
#     `exec` 让 xvfb-run 成为 PID 1 后，Xvfb 的父进程就是 PID 1，就绪信号永远不会发出，
#     `wait` 便无限等下去。对照实测：同一个脚本，父进程是 PID 1 时 `wait` 不返回，父进程
#     不是 PID 1 时立刻返回。
#     保留一个 shell 当 PID 1 还顺带负责回收子进程，这也是容器里通常推荐的形态。
set -eu

if [ -z "${DISPLAY:-}" ]; then
    # 不加 exec，见上方说明。
    # ⚠️ 退出码**不是**原样透传：xvfb-run 会保存子命令的退出码并重新退出它（健康路径上
    # 0/1/2 能穿过），但它自己的失败路径会用自身的退出码覆盖掉——Xvfb 起不来 → 1，
    # xauth 缺失 → 3，清理失败 → 5，参数错 → 6（见 Ubuntu noble 的 /usr/bin/xvfb-run）。
    # 其中 1 与契约里的「有差异」撞码：真触发时，「根本没跑起来」会被读成「模型变了」。
    # 已知限制，未修：健康镜像里这条路径不触发；要归一化就得放弃 xvfb-run、自己起
    # Xvfb，那会改到契约的执行路径，需要单独评审。
    xvfb-run -a --server-args="-screen 0 1600x1200x24" \
        python3 /app/caddiff/cli.py "$@"
    exit $?
fi

exec python3 /app/caddiff/cli.py "$@"
