"""FreeCAD 解释器解析的守卫测试（纯逻辑，不依赖 FreeCAD、不联网）。

背景（2026-10-10 读码发现）：`fcenv.freecad_python()` 原先只用 `os.path.exists` 判断
候选。而 Linux 发行版（apt / 官方 PPA）把 `<prefix>/bin/freecad-python3` 装成一份内嵌
Python 的 **GUI 应用**：它存在、可执行、`-c` 也能过，但喂它脚本文件时位置参数被读成
「要打开的文档」→ 启动整个 GUI 后**永不返回**（docs/pipeline.md 实测 300s 超时 rc=124、
零输出）。后果：没设 `FREECAD_PYTHON` 的 Linux 用户拿不到 README 承诺的可读报错，而是
卡到 `run_pipeline` 的子进程超时（默认 600s）。旧 README 更是直接教用户把
`FREECAD_PYTHON` 指向这个文件——所以**显式路径也必须校验**，只修自动发现会漏掉这批人。

判据因此改成「真的试跑一次」：显式路径要求能执行**脚本文件**，自动发现额外要求
`import FreeCAD` 真的成功（D-018 的教训：存在性检查会静默放行，必须实际执行一次）。

本文件钉住这条：证伪用例（GUI 应用式候选在解析阶段就被拒）+ 真跑用例（真的执行脚本文件）。
"""
import os
import subprocess
import sys
import types

import pytest

import fcenv


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    """任何用例都不该被开发机上真实的 FREECAD_* 影响。"""
    monkeypatch.delenv(fcenv.ENV_PYTHON, raising=False)
    monkeypatch.delenv(fcenv.ENV_HOME, raising=False)


def _not_an_interpreter(tmp_path, name="freecad-python3"):
    """造一个真实存在、但不是解释器的文件——代表 Linux 的那个 GUI 应用。

    两个平台上它都会被拒：Windows 上 `subprocess` 报 WinError 193（不是有效的应用），
    Linux 上临时文件无执行位、报 PermissionError。两者都是 OSError。
    """
    path = tmp_path / name
    path.write_text("#!/bin/sh\n# 这不是解释器\n", encoding="utf-8")
    return str(path)


def test_explicit_interpreter_is_accepted(monkeypatch):
    """真跑：系统 python 能执行脚本文件 → 原路径原样返回（不做任何改写）。"""
    monkeypatch.setenv(fcenv.ENV_PYTHON, sys.executable)
    assert fcenv.freecad_python() == sys.executable


def test_explicit_non_interpreter_is_rejected_and_names_the_trap(monkeypatch, tmp_path):
    """旧 README 教用户指向的那个文件，必须在**解析阶段**就被拒。

    判据是「不能等到子进程挂 600s」：拒绝时报错要同时给出路径、点明 GUI 应用这个坑、
    并带上修复指引。
    """
    fake = _not_an_interpreter(tmp_path)
    monkeypatch.setenv(fcenv.ENV_PYTHON, fake)

    with pytest.raises(RuntimeError) as exc:
        fcenv.freecad_python()

    message = str(exc.value)
    assert fake in message              # 哪个路径有问题
    assert "freecad-python3" in message  # 坑叫什么
    assert fcenv.ENV_PYTHON in message   # 怎么修


def test_explicit_missing_path_still_says_missing(monkeypatch, tmp_path):
    """原有行为不变：路径不存在时说的是「不存在」，不是「不是解释器」。"""
    missing = str(tmp_path / "nowhere" / "python.exe")
    monkeypatch.setenv(fcenv.ENV_PYTHON, missing)

    with pytest.raises(RuntimeError, match="不存在"):
        fcenv.freecad_python()


def test_probe_hands_the_candidate_a_script_file_not_dash_c(monkeypatch):
    """判据必须是「**脚本文件**能执行」——`-c` 拦不住 GUI 应用（它能过 -c）。"""
    seen = {}

    def fake_run(cmd, **kwargs):
        seen["cmd"] = list(cmd)
        seen["source"] = open(cmd[1], encoding="utf-8").read()
        seen["existed"] = os.path.exists(cmd[1])
        return subprocess.CompletedProcess(cmd, 0)

    monkeypatch.setattr(fcenv.subprocess, "run", fake_run)

    assert fcenv._runs_script_file(sys.executable, "print('probe')\n") is True
    assert seen["cmd"][0] == sys.executable
    assert "-c" not in seen["cmd"]
    assert seen["existed"] and seen["cmd"][1].endswith(".py")
    assert seen["source"] == "print('probe')\n"
    assert not os.path.exists(seen["cmd"][1])  # 临时文件必须清掉


def test_probe_rejects_a_candidate_that_never_returns(monkeypatch):
    """挂住的候选（GUI 应用的实际表现）必须被超时上限拒掉，而不是把流水线一起挂住。"""
    monkeypatch.setattr(fcenv, "_PROBE_TIMEOUT", 0.3)

    assert fcenv._runs_script_file(
        sys.executable, "import time\ntime.sleep(30)\n") is False


def test_discovery_probe_demands_a_real_freecad_import(monkeypatch, tmp_path):
    """自动发现是在猜，判据高于显式路径：试跑源码要真的 `import FreeCAD` 才算数。"""
    good = str(tmp_path / "candidate-python")
    calls = []

    def spy(python_exe, source):
        calls.append((python_exe, source))
        return python_exe == good

    monkeypatch.setattr(fcenv, "_runs_script_file", spy)
    monkeypatch.setattr(
        fcenv, "_LINUX_CANDIDATES",
        ("/usr/lib/freecad/bin/freecad-python3", good))
    monkeypatch.setattr(fcenv, "sys", types.SimpleNamespace(platform="linux"))

    assert fcenv.freecad_python() == good
    assert [c[0] for c in calls] == ["/usr/lib/freecad/bin/freecad-python3", good]
    assert "import FreeCAD" in calls[0][1]  # 被拒的候选也要带着这条判据去试


def test_freecad_home_candidate_must_also_pass_the_probe(monkeypatch, tmp_path):
    """`FREECAD_HOME` 拼出来的同样是发行版布局那个文件：不能只看它存在。"""
    (tmp_path / "bin").mkdir()
    (tmp_path / "bin" / "freecad-python3").write_text(
        "#!/bin/sh\n# 这不是解释器\n", encoding="utf-8")
    monkeypatch.setenv(fcenv.ENV_HOME, str(tmp_path))
    monkeypatch.setattr(fcenv, "_LINUX_CANDIDATES", ())
    monkeypatch.setattr(fcenv.shutil, "which", lambda name: None)

    with pytest.raises(RuntimeError) as exc:
        fcenv.freecad_python()

    assert fcenv.ENV_PYTHON in str(exc.value)


def test_discovery_without_any_working_candidate_raises_the_hint(monkeypatch):
    """全被拒时给的是那条修复指引（含正解：系统 python3 + PYTHONPATH），不是静默回退。"""
    monkeypatch.setattr(fcenv, "_LINUX_CANDIDATES", ("/nonexistent/bin/freecad-python3",))
    monkeypatch.setattr(fcenv.shutil, "which", lambda name: None)
    monkeypatch.setattr(fcenv, "sys", types.SimpleNamespace(platform="linux"))

    with pytest.raises(RuntimeError) as exc:
        fcenv.freecad_python()

    message = str(exc.value)
    assert fcenv.ENV_PYTHON in message
    assert "PYTHONPATH=/usr/lib/freecad/lib" in message
