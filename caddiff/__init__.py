"""caddiff —— `git diff for CAD assemblies`。

包内模块之间一律用**扁平 import**（`import fcenv` 而不是 `from . import fcenv`）。
why：几何/渲染模块必须由 **FreeCAD 自带的 Python 解释器**以「独立脚本」方式启动
（`<FREECAD_PYTHON> <pkg>/geom_diff.py ...`），那个解释器里没有安装本包，相对 import
会直接失败。入口（cli.py）负责把包目录塞进 `sys.path`，让扁平 import 在「源码运行」
与「pip 安装」两种形态下都成立。改动 import 风格前先读 `docs/pipeline.md`。

⚠️ 本文件是**唯一例外**：它在 cli.py 注入 `sys.path` **之前**执行，所以扁平 import
`from version import ...` 在 pip 安装形态下必炸（`version` 不在 site-packages 顶层），
实测 `pip install -e .` 后 `caddiff --version` 直接 ModuleNotFoundError。
故这里用「相对导入优先、扁平导入兜底」：作为包被导入时相对导入总是成立；只有当
caddiff/ 目录本身被塞进 `sys.path` 时才回退到扁平导入。
"""
try:
    from .version import __version__  # noqa: F401  （作为包导入：pip 安装 / 源码根目录）
except ImportError:  # pragma: no cover - 仅当 caddiff/ 目录自身在 sys.path 上
    from version import __version__  # noqa: F401

__all__ = ["__version__"]
