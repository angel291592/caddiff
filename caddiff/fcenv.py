r"""FreeCAD 解释器与库路径解析——全项目唯一真相源。

why 需要它：早期每个脚本各自写死 Windows 发行版目录
（``freecad/FreeCAD_1.1.3-Windows-x86_64-py311``），同一份路径在 4 个文件里各写一遍，
Linux/容器下会静默走错解释器或直接 FileNotFoundError；改一次版本号要动四处，必然漂移。

解析顺序（第一个命中即用）：
  1. 环境变量 ``FREECAD_PYTHON``——Docker 镜像与本地开发都靠它
  2. 环境变量 ``FREECAD_HOME`` → ``<home>/bin/freecad-python3``（Linux 发行版布局）
  3. 常见 Linux 安装路径
  4. ``PATH`` 上的 ``freecad-python3`` / ``FreeCADCmd`` / ``freecadcmd``

**找不到时抛错，不静默回退到当前解释器**——静默回退会在 ``import FreeCAD`` 处才炸，
报错点离真正原因很远（用户会以为项目坏了，实际只是没装 FreeCAD）。

本模块只在【需要拉起 FreeCAD 子进程】的地方使用。脚本自身已经跑在 FreeCAD 解释器里时
（geom_diff / render_diff / boolean_worker），用 ``derive_bin_lib(sys.executable)`` 即可。
"""
import os
import shutil
import sys

ENV_PYTHON = "FREECAD_PYTHON"
ENV_HOME = "FREECAD_HOME"

# Linux 发行版（apt / FreeCAD 官方 PPA）的常见位置
_LINUX_CANDIDATES = (
    "/usr/lib/freecad/bin/freecad-python3",
    "/usr/lib/freecad-python3/bin/freecad-python3",
    "/usr/local/lib/freecad/bin/freecad-python3",
)

_PATH_NAMES = ("freecad-python3", "FreeCADCmd", "freecadcmd")

_HINT = (
    "未找到 FreeCAD 的 Python 解释器。请任选一种方式修复：\n"
    "  1) 设置环境变量 FREECAD_PYTHON 指向 FreeCAD 自带的 python 可执行文件；\n"
    "     例（Windows）: set FREECAD_PYTHON=C:\\FreeCAD 1.1\\bin\\python.exe\n"
    "     例（Linux）  : export FREECAD_PYTHON=/usr/lib/freecad/bin/freecad-python3\n"
    "  2) 安装 FreeCAD（apt install freecad / 官方安装包）后重试；\n"
    "  3) 直接用官方 Docker 镜像跑，镜像内已配置好该变量。"
)


def freecad_python():
    """返回 FreeCAD 自带 Python 解释器的路径。找不到就抛 RuntimeError（含修复指引）。"""
    env = (os.environ.get(ENV_PYTHON) or "").strip()
    if env:
        if not os.path.exists(env):
            raise RuntimeError(
                f"{ENV_PYTHON} 指向的路径不存在: {env}\n{_HINT}")
        return env

    home = (os.environ.get(ENV_HOME) or "").strip()
    if home:
        cand = os.path.join(home, "bin", "freecad-python3")
        if os.path.exists(cand):
            return cand

    if sys.platform != "win32":
        for cand in _LINUX_CANDIDATES:
            if os.path.exists(cand):
                return cand

    for name in _PATH_NAMES:
        found = shutil.which(name)
        if found:
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
