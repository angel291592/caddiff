# 示例

[English](README.md) | 简体中文

本目录中的一切都是**合成样例(手动生成)**。它们由 [`caddiff/make_moved_fixture.py`](../caddiff/make_moved_fixture.py) 从基本体(长方体)生成——本仓库任何地方都没有客户 CAD 数据,以后也永远不该有。

```
fixtures/
  moved_old.stp        三个零件:BASEPLATE、SLIDER、BRACKET
  moved_new.stp        同一装配体,含两处刻意设置的改动
expected/
  report.html          `caddiff diff` 在该样例对上的产出
  report.md            同一份报告的 Markdown 版本
  images/*.png         总览 + 每处改动的旧/新特写
  summary.json         期望输出值,以机器可读形式呈现
```

## 这对样例包含什么

| 零件 | 改动 |
|---|---|
| `BASEPLATE` | 无——对照组,它必须**不**出现在输出中 |
| `SLIDER` | 平移 5 mm → `moved` |
| `BRACKET` | 切掉一个 4×12×4 mm 的方块 → `shape_changed`,移除 192 mm³ |

因此,期望输出正好是 **2 处几何差异** 与退出码 **1**:

```console
$ caddiff diff fixtures/moved_old.stp fixtures/moved_new.stp -o out
$ echo $?
1
$ python -c "import json;print(json.load(open('out/diff_manifest.json'))['summary']['geometry_diff_count'])"
2
```

## 重新生成

渲染是最耗时的部分(3 零件装配体上约 75 s,无 GPU),因此这些文件以提交入库的方式保存,而不是每次运行都在 CI 中生成。

```console
$ export FREECAD_PYTHON=/path/to/freecad/bin/freecad-python3
$ python caddiff/make_moved_fixture.py examples/fixtures
$ caddiff diff examples/fixtures/moved_old.stp examples/fixtures/moved_new.stp -o out
```

然后将 `out/report.html`、`out/report.md` 和 `out/images/` 覆盖复制到 `expected/`。

`summary.json` 是 CI 断言的依据——它刻意**不含绝对路径**,因此可以在不同机器之间移植:

```json
{
  "exit_code": 1,
  "geometry_diff_count": 2,
  "by_change_type": { "moved": 1, "shape_changed": 1 }
}
```

## 本样例刻意演练的两件事

**1. 两个版本都从同名文档导出。** 这不是表面功夫:STEP 导出会把文档名写入顶层 `PRODUCT` 记录,而 BOM 层按 `PRODUCT` 名比对零件。若两个文档的名字不同(`MovedOld` / `MovedNew`),工具就会报告一个 added 零件和一个 removed 零件——而这是几何上并不存在的差异。这是一个值得了解的真实限制:**顶层装配体的 product 参与 BOM 比较**,因此在修订版之间重命名装配体,就会表现为一次 add + remove。

**2. 装配体容器被报告为 skipped,而不是作为差异。** STEP 导入会把顶层装配体构建为一个 `App::Part` 容器,该容器会作为一个 BOM 候选出现。它的“形状”是全部子对象的并集,因此*任何*子对象发生变化都会让它也报告一处差异——把“2 处改动”变成“3 处改动”。因此,`caddiff` 会跳过那些子对象本身也是候选的容器,并如实记录:

```json
"skipped_parts": [ { "name": "MovedFixture", "reason": "assembly_container" } ]
```

没有参与比较的子对象的容器仍按普通零件处理,因此真实的零件绝不会以这种方式被静默丢弃。
