"""FreeCAD 26 import 顺序的守卫测试（纯 AST 解析，不依赖 FreeCAD、不联网）。

背景（FreeCAD 26.3.0，conda-forge，Python 3.11 实测）：脚本以 ``__main__`` 方式运行且
``import FreeCAD`` 时，26.x 会**清掉此前绑定的模块级名字**（``os`` / ``sys`` 存活）。于是
「顶层先 import json，再 import FreeCAD，函数里用 json」的写法在 26.x 上会在第一次使用时
抛 ``NameError``、该步骤以非零退出、流水线报 exit 2，中间产物一个都不写。

这个缺陷静态检查看不见（名字在源码里确实先绑定了），只有真的跑在 26.x 上才会现形。本条
测试把规则钉在 CI 里，代价接近于零：

1. ``import FreeCAD`` 之前只允许 ``os`` / ``sys`` / ``fcenv``（``fcenv`` 必须靠前，负责注入
   FreeCAD 的 bin/lib 路径）；
2. 在 ``import FreeCAD`` 之前绑定、又在它之后被读取、且不在存活名单里的名字，必须在它之后
   **重新绑定**（例如再 ``import fcenv`` 一次）。

实测依据与修法见 docs/pipeline.md 坑 K1（中英双语版本内容一致）。
"""
import ast
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
_PKG_DIR = _REPO_ROOT / "caddiff"

# 26.x 的 import 清理中存活的名字（实测：os / sys），在 import FreeCAD 之后可以继续直接用。
_SURVIVE = frozenset({"os", "sys"})
# 允许出现在 import FreeCAD 之前的模块名；fcenv 是路径解析的真相源，必须早于 FreeCAD。
_ALLOWED_BEFORE = _SURVIVE | {"fcenv"}

# 由 FreeCAD 解释器以「独立脚本」方式启动的模块：新增同类脚本必须加进这张表。
_FREECAD_SCRIPTS = [
    "boolean_worker.py",
    "geom_diff.py",
    "make_moved_fixture.py",
    "perf_probe.py",
    "render_diff.py",
]


def _freecad_import_line(tree):
    """``import FreeCAD`` 的行号；没有就返回 None。

    有的脚本把 FreeCAD import 放在 ``main()`` 里（``boolean_worker.py``），所以这里用 walk。
    """
    lines = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import) and any(a.name == "FreeCAD" for a in node.names):
            lines.append(node.lineno)
        elif isinstance(node, ast.ImportFrom) and node.module == "FreeCAD":
            lines.append(node.lineno)
    return min(lines) if lines else None


def _module_level_bindings(tree):
    """模块顶层 import 产生的绑定：[(name, lineno)]，同一名字出现多次就记多次。"""
    bindings = []
    for node in tree.body:
        if isinstance(node, ast.Import):
            for alias in node.names:
                bindings.append((alias.asname or alias.name.split(".")[0], node.lineno))
        elif isinstance(node, ast.ImportFrom):
            for alias in node.names:
                bindings.append((alias.asname or alias.name, node.lineno))
    return bindings


def _import_bindings_anywhere(tree):
    """任意作用域的 import 绑定：[(name, lineno)]。函数内的 import 绑定的是局部名，清理不到。"""
    bindings = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                bindings.append((alias.asname or alias.name.split(".")[0], node.lineno))
        elif isinstance(node, ast.ImportFrom):
            for alias in node.names:
                bindings.append((alias.asname or alias.name, node.lineno))
    return bindings


def _loads_after(tree, lineno):
    """出现在 ``lineno`` 之后（含嵌套作用域）的每个 ``Name`` 读取。"""
    for node in ast.walk(tree):
        if (isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load)
                and node.lineno > lineno):
            yield node


@pytest.mark.parametrize("script", _FREECAD_SCRIPTS)
def test_freecad_scripts_import_order(script):
    path = _PKG_DIR / script
    tree = ast.parse(path.read_text(encoding="utf-8"))

    freecad_line = _freecad_import_line(tree)
    assert freecad_line is not None, f"{script}: 顶层找不到 import FreeCAD"

    bindings = _module_level_bindings(tree)
    before = {name for name, line in bindings if line < freecad_line}

    early = sorted(before - _ALLOWED_BEFORE)
    assert not early, (
        f"{script}: {early} 在 import FreeCAD 之前绑定；FreeCAD 26 会清掉这些名字，"
        f"必须挪到 import FreeCAD 之后（坑 K1）"
    )

    rebound = {name for name, line in _import_bindings_anywhere(tree) if line > freecad_line}
    suspects = before - _SURVIVE
    offenders = sorted(
        {(node.id, node.lineno) for node in _loads_after(tree, freecad_line)
         if node.id in suspects and node.id not in rebound}
    )
    assert not offenders, (
        f"{script}: 这些名字在 import FreeCAD 之前绑定、之后仍在使用，但之后没有再绑定 → "
        f"FreeCAD 26 上会是 NameError（坑 K1）：{offenders}"
    )
