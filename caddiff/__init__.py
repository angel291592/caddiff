"""caddiff —— `git diff for CAD assemblies`。

包内模块之间一律用**扁平 import**（`import fcenv` 而不是 `from . import fcenv`）。
why：几何/渲染模块必须由 **FreeCAD 自带的 Python 解释器**以「独立脚本」方式启动
（`<FREECAD_PYTHON> <pkg>/geom_diff.py ...`），那个解释器里没有安装本包，相对 import
会直接失败。入口（cli.py）负责把包目录塞进 `sys.path`，让扁平 import 在「源码运行」
与「pip 安装」两种形态下都成立。改动 import 风格前先读 `docs/pipeline.md`。
"""
from version import __version__  # noqa: F401  （扁平 import：见模块 docstring）

__all__ = ["__version__"]
