"""pytest 公共配置。

why 要手工插 sys.path：`caddiff/` 是一个「既可被 pip 安装、又会被 FreeCAD 解释器当独立
脚本执行」的目录（见 caddiff/__init__.py 的 docstring），模块之间用**扁平 import**。
测试直接跑在源码树上，所以必须把该目录塞进 sys.path，否则 `import run_pipeline` 失败。

⚠️ 本目录下的测试**不得**依赖 FreeCAD、不得联网、不得调用任何外部 API（AGENTS.md §7）。
FreeCAD 相关的验证属于端到端冒烟，不在这里。
"""
import os
import sys

_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
_PKG_DIR = os.path.join(_REPO_ROOT, "caddiff")
# tools/ 放进来是为了让 test_sensitive_gate.py 能 import 脱敏闸门本身
_TOOLS_DIR = os.path.join(_REPO_ROOT, "tools")

for _p in (_PKG_DIR, _TOOLS_DIR, _REPO_ROOT):
    if _p not in sys.path:
        sys.path.insert(0, _p)
