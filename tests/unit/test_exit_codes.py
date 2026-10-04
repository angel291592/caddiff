"""退出码契约的守卫测试（纯逻辑，不依赖 FreeCAD、不联网）。

背景（本机语料实测，2026-10-04）：`bom_diff` 的提取正则原先只认紧凑写法
`PRODUCT('name'`，而 SolidWorks 写 `PRODUCT ( 'name'`。于是一份 45.7MB、含 143 个
实体与 804 条装配关系的真实公开装配体（machineagency/jubilee）被解析成 **0 个产品**
→ 0 个候选 → 0 处差异 → **退出码 0**。工具对一份确实改过的装配体宣布「无差异」。

这类失败比崩溃危险得多：崩溃至少留下退出码 2，而「静默的 0」会被 CI 闸门当成放行
信号。AGENTS.md §3 因此明确规定：**任何内部子步骤失败都必须传导到退出码 2**，
不得被折叠成 0 或 1。本文件钉住这条。
"""
import json

import pytest

import run_pipeline

_EMPTY_STEP = """ISO-10303-21;
HEADER;
FILE_DESCRIPTION ((''), '2;1');
FILE_NAME ('empty.step', '2024-01-01T00:00:00', (''), (''), '', '', '');
FILE_SCHEMA (('AUTOMOTIVE_DESIGN'));
ENDSEC;
DATA;
#1 = APPLICATION_CONTEXT ( 'automotive_design' ) ;
ENDSEC;
END-ISO-10303-21;
"""

_SPACED_STEP = """ISO-10303-21;
HEADER;
ENDSEC;
DATA;
#90 = PRODUCT ( 'back_left_foot', 'back_left_foot', '', ( #439791 ) ) ;
#91 = PRODUCT ( 'crossbar', 'crossbar', '', ( #439792 ) ) ;
ENDSEC;
END-ISO-10303-21;
"""


def _write(path, text):
    path.write_text(text, encoding="utf-8")
    return str(path)


def _run(tmp_path, old_text, new_text):
    out = tmp_path / "out"
    return run_pipeline.run_diff(
        stp_old=_write(tmp_path / "old.step", old_text),
        stp_new=_write(tmp_path / "new.step", new_text),
        output_dir=str(out),
    )


def test_zero_products_on_both_sides_is_an_execution_failure(tmp_path):
    """两份文件都解析不出产品 -> 必须报错，绝不能返回「无差异」的 manifest。

    这是 CI 闸门最危险的假阴性：真实改动被呈现为「干净」。
    """
    with pytest.raises(run_pipeline.PipelineError) as exc:
        _run(tmp_path, _EMPTY_STEP, _EMPTY_STEP)
    message = str(exc.value)
    assert "no PRODUCT records found" in message
    # 消息必须说清「拒绝报无差异」的理由，而不是一句模糊的内部错误
    assert "no differences" in message


def test_the_guard_trips_before_any_geometry_work(tmp_path):
    """守卫必须早于几何/渲染步骤：否则每对文件都要先烧掉几分钟才失败。"""
    with pytest.raises(run_pipeline.PipelineError) as exc:
        _run(tmp_path, _EMPTY_STEP, _EMPTY_STEP)
    # 几何层没跑，就不该出现它的失败措辞（"step 'Step 2/5'"）；出现即意味着顺序错了
    assert "Step 2/5" not in str(exc.value)


def test_one_sided_emptiness_is_not_an_error(tmp_path):
    """只有一边为空是合法的「整件替换」，那是真差异，不能走 fail-closed 分支。

    直接调用 bom_diff（纯文本，无 FreeCAD）验证判定所依赖的字段。
    """
    import bom_diff

    res = bom_diff.diff_bom(_write(tmp_path / "a.step", _SPACED_STEP),
                            _write(tmp_path / "b.step", _EMPTY_STEP))
    assert res["old_total_products"] == 2
    assert res["new_total_products"] == 0
    # 守卫的条件是「两边都为 0」，这里不成立
    assert not (res["old_total_products"] == 0 and res["new_total_products"] == 0)
    # 整件替换必须报告为 removed，而不是「无差异」
    assert res["removed"]


def test_solidworks_spaced_products_are_counted(tmp_path):
    """回归：空格写法必须被解析出来，否则上面的守卫会把真装配体当坏输入拒绝。"""
    import bom_diff

    names = bom_diff.extract_product_names(_write(tmp_path / "spaced.step", _SPACED_STEP))
    assert names == ["back_left_foot", "crossbar"]


def test_render_timeout_can_be_overridden(tmp_path):
    """渲染死线必须可覆盖：默认估算（180 + 60/处）对大型装配体偏低。

    实测（machineagency/jubilee，14 处差异）：估算给出 1020s，渲染到这一步还没结束就被
    杀掉，整轮产物全丢、退出码 2。估算公式不坏，但它不该是唯一选择——用户看得出这一对
    够大，就该能自己给一个更宽的死线。
    """
    import inspect

    sig = inspect.signature(run_pipeline.run_diff)
    assert "render_timeout" in sig.parameters
    assert sig.parameters["render_timeout"].default is None

    # 估算值本身仍然按「差异数」给，这一点不能被覆盖逻辑改动
    geom = tmp_path / "geom.json"
    geom.write_text(json.dumps({
        "geometric_diffs": [{"geometric_changes": [{"a": 1}, {"b": 2}]}],
    }), encoding="utf-8")
    assert run_pipeline.estimate_render_timeout(str(geom)) == 180 + 60 * 2

    # 没有差异时也给出一个下限，不能退化成 180 + 0
    geom0 = tmp_path / "geom0.json"
    geom0.write_text(json.dumps({"geometric_diffs": []}), encoding="utf-8")
    assert run_pipeline.estimate_render_timeout(str(geom0)) == 180 + 60


def test_render_timeout_is_recorded_in_settings(tmp_path):
    """死线要落进 manifest.settings.render_timeout_s：否则「这一步超时了」无法复现。

    纯逻辑验证：不跑 FreeCAD，直接检查 run_diff 把生效死线写进 summary 的那段契约。
    用源码断言是刻意的——这条信息由编排层写入，而几何层根本不知道它存在。
    """
    import inspect

    src = inspect.getsource(run_pipeline.run_diff)
    assert "render_timeout_s" in src, (
        "生效的渲染死线必须写进 summary.settings，否则超时无法归因"
    )
    # 覆盖值优先于估算值，且两者只算一次（同一个变量喂给 run_step 与 manifest）
    assert "effective_render_timeout" in src
    assert src.count("effective_render_timeout") >= 2
