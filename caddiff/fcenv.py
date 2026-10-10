r"""FreeCAD 解释器与库路径解析——全项目唯一真相源。

why 需要它：早期每个脚本各自写死 Windows 发行版目录
（``freecad/FreeCAD_1.1.3-Windows-x86_64-py311``），同一份路径在 4 个文件里各写一遍，
Linux/容器下会静默走错解释器或直接 FileNotFoundError；改一次版本号要动四处，必然漂移。

解析顺序（第一个**试跑通过**的候选胜出）：
  1. 环境变量 ``FREECAD_PYTHON``——Docker 镜像与本地开发都靠它
  2. 环境变量 ``FREECAD_HOME`` → ``<home>/bin/freecad-python3``（Linux 发行版布局）
  3. 常见 Linux 安装路径
  4. ``PATH`` 上的 ``freecad-python3`` / ``FreeCADCmd`` / ``freecadcmd``

**候选必须真的试跑一次才算数**（``_runs_script_file``）：第 2–4 条本质是在猜，猜错的
代价是把 Linux 发行版的 ``<prefix>/bin/freecad-python3`` 当成解释器——那是一份内嵌
Python 的 **GUI 应用**，喂它脚本会启动界面并永不返回（D-022 实测 300s 超时、零输出）。
存在性检查拦不住它（它确实存在、确实可执行、还能过 ``-c``）。

**找不到时抛错，不静默回退到当前解释器**——静默回退会在 ``import FreeCAD`` 处才炸，
报错点离真正原因很远（用户会以为项目坏了，实际只是没装 FreeCAD）。

本模块只在【需要拉起 FreeCAD 子进程】的地方使用。脚本自身已经跑在 FreeCAD 解释器里时
（geom_diff / render_diff / boolean_worker），用 ``derive_bin_lib(sys.executable)`` 即可。
"""
import os
import shutil
import subprocess
import sys
import tempfile

ENV_PYTHON = "FREECAD_PYTHON"
ENV_HOME = "FREECAD_HOME"

# Linux 发行版（apt / FreeCAD 官方 PPA）的常见位置
_LINUX_CANDIDATES = (
    "/usr/lib/freecad/bin/freecad-python3",
    "/usr/lib/freecad-python3/bin/freecad-python3",
    "/usr/local/lib/freecad/bin/freecad-python3",
)

_PATH_NAMES = ("freecad-python3", "FreeCADCmd", "freecadcmd")

# 候选试跑的超时上限（秒）。真解释器跑完一个空脚本是毫秒级，只有「不是解释器」的
# 候选才会磨到这个上限——它同时也是**唯一**能拦住 GUI 应用的手段（见 _runs_script_file）。
_PROBE_TIMEOUT = 15

# 显式（FREECAD_PYTHON）只验证到「能执行脚本文件」为止：路径是用户自己选的，不再替他
# 假设更多。试跑源码必须是**脚本文件**内容——``-c`` 拦不住 GUI 应用（D-022 实测）。
_PROBE_SRC = "raise SystemExit(0)\n"

# 自动发现（第 2–4 条）本质是在猜，要求提高到「真的能 import FreeCAD」，否则把
# FreeCADCmd 之类只能跑一半流水线的候选也认下来，用户会在渲染那一步才撞墙。
_DISCOVER_PROBE_SRC = "import FreeCAD\nraise SystemExit(0)\n"

_HINT = (
    "没有可用的 FreeCAD Python 解释器。请任选一种方式修复：\n"
    "  1) 设置环境变量 FREECAD_PYTHON 指向**能执行脚本文件**的 Python 解释器；\n"
    "     例（Windows）: set FREECAD_PYTHON=C:\\FreeCAD 1.1\\bin\\python.exe\n"
    "     例（Linux）  : export FREECAD_PYTHON=/usr/bin/python3\n"
    "                    export PYTHONPATH=/usr/lib/freecad/lib\n"
    "     ⚠️ Linux 上**不要**指向 /usr/lib/freecad/bin/freecad-python3：那是一份内嵌 Python 的\n"
    "        GUI 应用，会把脚本参数当成「要打开的文档」，启动整个 GUI 后永不返回（实测挂死）。\n"
    "  2) 安装 FreeCAD（apt install freecad / 官方安装包）后重试；\n"
    "  3) 直接用官方 Docker 镜像跑，镜像内已配置好该变量。"
)


def _runs_script_file(python_exe, source):
    """把一份临时脚本文件交给候选执行，**跑完且退出码为 0** 才算数。

    why 不能只看 ``os.path.exists``：Linux 发行版把 ``<prefix>/bin/freecad-python3``
    装成一份内嵌 Python 的 **GUI 应用**——它存在、可执行、``-c`` 也能过，但位置参数会被
    读成「要打开的文档」，于是启动整个 GUI 后**永不返回**（D-022 实测 300s 超时 rc=124、
    零输出）。唯一能区分「解释器」与「GUI 应用」的判据，就是让它真的执行一个**脚本文件**
    （``deploy/Dockerfile`` 的构建期断言用的也是这一条）。

    连临时文件都建不出来（只读 tmp 之类的环境故障）时返回 True：那是环境问题，不能据此
    把候选判死——本函数负责识别已知的坏解释器，不是当权限闸门。
    """
    try:
        fd, script = tempfile.mkstemp(suffix=".py", prefix="caddiff_probe_")
    except OSError:
        return True
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(source)
        proc = subprocess.run(
            [python_exe, script], timeout=_PROBE_TIMEOUT,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return proc.returncode == 0
    except (subprocess.TimeoutExpired, OSError):
        return False
    finally:
        try:
            os.unlink(script)
        except OSError:
            pass


def freecad_python():
    """返回**试跑通过**的 FreeCAD Python 解释器路径。找不到就抛 RuntimeError（含修复指引）。"""
    env = (os.environ.get(ENV_PYTHON) or "").strip()
    if env:
        if not os.path.exists(env):
            raise RuntimeError(
                f"{ENV_PYTHON} 指向的路径不存在: {env}\n{_HINT}")
        if not _runs_script_file(env, _PROBE_SRC):
            raise RuntimeError(
                f"{ENV_PYTHON} 指向的路径不是可用的 Python 解释器: {env}\n"
                f"（它无法执行脚本文件。Linux 发行版的 <freecad>/bin/freecad-python3 正是这种：\n"
                f"  那是 GUI 应用，把脚本交给它会启动界面并永不返回。）\n{_HINT}")
        return env

    home = (os.environ.get(ENV_HOME) or "").strip()
    if home:
        cand = os.path.join(home, "bin", "freecad-python3")
        if _runs_script_file(cand, _DISCOVER_PROBE_SRC):
            return cand

    if sys.platform != "win32":
        for cand in _LINUX_CANDIDATES:
            if _runs_script_file(cand, _DISCOVER_PROBE_SRC):
                return cand

    for name in _PATH_NAMES:
        found = shutil.which(name)
        if found and _runs_script_file(found, _DISCOVER_PROBE_SRC):
            return found

    raise RuntimeError(_HINT)


def derive_bin_lib(python_exe):
    """由解释器路径推导 (bin_dir, lib_dir)，用于给子进程注入 ``sys.path``。

    Windows 发行版布局：``<install>/bin/python.exe`` + ``<install>/lib``
    Linux 发行版布局  ：``<prefix>/bin/freecad-python3`` + ``<prefix>/lib``（可能不存在）

    只返回**真实存在**的目录，不存在则给空串——空串是 ``boolean_worker.py`` 的合法输入
    （Linux 下 FreeCAD 模块在系统 site-packages 里，本就无需注入）。
    """
    if not python_exe:
        return "", ""
    bin_dir = os.path.dirname(os.path.abspath(python_exe))
    lib_dir = os.path.join(os.path.dirname(bin_dir), "lib")
    return (bin_dir if os.path.isdir(bin_dir) else "",
            lib_dir if os.path.isdir(lib_dir) else "")


def inject_freecad_paths(python_exe=None):
    """把 FreeCAD 的 bin/lib 注入 ``sys.path``（仅当目录存在时）。

    供**自己就跑在 FreeCAD 解释器里**的脚本在启动最早期调用；对系统 Python 调用无意义，
    因为真正的问题是找不到 FreeCAD 模块，而不是路径没注入。
    """
    bin_dir, lib_dir = derive_bin_lib(python_exe or sys.executable)
    for d in (lib_dir, bin_dir):
        if d and d not in sys.path:
            sys.path.insert(0, d)
    return bin_dir, lib_dir
