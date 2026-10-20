"""boolean_worker 的失败路径必须留下结构化结果（不依赖 FreeCAD、不联网）。

为什么这条值得常驻：`boolean_worker.py` 自己的模块说明写死了「子进程里的任何失败都必须
变成父进程能读到的结构化结果，否则父进程只看到非零 returncode，无法区分『几何算不出来』
与『进程被 kill』」。这条约束在 v0.4.0 上被一个 import 顺序修复（issue #11 / PR #12）打
破了：为了让 FreeCAD 26 不再清掉 `json`，`import json` 被移进了 `main()`；而模块级异常
处理器仍在用全局名 `json.dump`，于是子进程一旦抛异常，处理器自己再抛 `NameError`——
`out.json` 先被 `open()` 截断成 0 字节，父进程 `geom_diff` 读它时只能记
`worker_crashed(rc=1)`，原始错误（`importBrep` 失败、FreeCAD 导入失败等）全部丢失。

这个缺陷在任何 FreeCAD 版本上都成立（0.21 镜像同样中招），但它的触发前提是「子进程恰好
抛异常」，所以正常路径的 115 个测试全绿也看不见它。本测试用桩 FreeCAD/Part 让 `main()`
在第一步就抛错，断言 `out.json` 仍是可解析的 `{"ok": false, "error": ...}`。
"""
import json
import os
import subprocess
import sys
from pathlib import Path

CADDIFF = Path(__file__).resolve().parents[2] / "caddiff"


def test_boolean_worker_writes_structured_error_when_the_shape_step_raises(tmp_path):
    stubs = tmp_path / "stubs"
    (stubs / "FreeCAD").mkdir(parents=True)
    (stubs / "Part").mkdir(parents=True)
    (stubs / "FreeCAD" / "__init__.py").write_text("", encoding="utf-8")
    (stubs / "Part" / "__init__.py").write_text(
        "def Shape(*args, **kwargs):\n"
        "    raise RuntimeError('stub Part: shape unavailable')\n",
        encoding="utf-8",
    )

    out_json = tmp_path / "boolean_out.json"
    env = dict(os.environ)
    # 只给桩模块：真实 FreeCAD 若装在系统里也不能参与，否则结果不可复现。
    env["PYTHONPATH"] = str(stubs)

    proc = subprocess.run(
        [sys.executable, str(CADDIFF / "boolean_worker.py"), "",
         "", "old.brep", "new.brep", str(out_json)],
        capture_output=True, text=True, env=env, timeout=120,
    )

    assert proc.returncode != 0, "a worker whose shape step raises must exit non-zero"
    assert "NameError" not in proc.stderr, (
        "the module-level error handler itself raised NameError, so the child's original "
        "error is lost and the parent can only report worker_crashed. A module-level name "
        "the handler relies on is no longer bound (FreeCAD 26 clears those bound before "
        "`import FreeCAD`; the local imports in main() do not bind globals). Add the import "
        "inside the handler itself.\nstderr:\n" + proc.stderr
    )
    assert out_json.is_file(), (
        "the error handler must write the structured result file; the parent reads it to "
        "tell 'geometry failed' apart from 'process was killed'"
    )
    payload = json.loads(out_json.read_text(encoding="utf-8"))
    assert payload["ok"] is False
    assert "RuntimeError" in payload["error"], (
        f"the original exception must survive into out.json, got: {payload!r}"
    )
