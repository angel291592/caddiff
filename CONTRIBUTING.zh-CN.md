# 为 caddiff 做贡献

[English](CONTRIBUTING.md) | 简体中文

感谢你抽出时间参与贡献。

`caddiff` 是 `git diff for CAD assemblies`:由你给它两个版本的 STEP/STP 装配体,
它会还给你一张差异高亮图、一份人类可读的 diff 报告(HTML/Markdown),
以及一份机器可读的 manifest(JSON)。

有两份配套文档与本文件同样重要:

- [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md) - 项目每个空间中应遵循的行为。
- [SECURITY.zh-CN.md](SECURITY.zh-CN.md) - 如何私下报告漏洞。安全问题绝不要公开地开 issue。

## 参与贡献的方式

- 报告 bug 时附上最小复现(见下文的数据规则——绝不要附上真实客户模型)。
- 改进 diff 的质量:零件配对、变更分类、渲染。
- 改进文档,包括本文件。
- 增加测试。小而聚焦、可离线运行的测试是我们能得到的最有价值的贡献。

## 用 Docker 运行(一条命令)

已发布的镜像中已包含 FreeCAD,因此这是一条最短路径:

```bash
docker run --rm -v "$PWD:/work" ghcr.io/<owner>/<repo>:latest diff OLD.stp NEW.stp -o report/
```

在 Windows PowerShell 上:

```powershell
docker run --rm -v ${PWD}:/work ghcr.io/<owner>/<repo>:latest diff OLD.stp NEW.stp -o report/
```

退出码是公开契约的一部分:

| 退出码 | 含义 |
|---|---|
| `0` | 无差异。 |
| `1` | 检出差异。 |
| `2` | 执行失败(输入不可读、超时、子进程崩溃等)。 |

如果你改动了任何可能影响这些退出码的内容,请在 PR 中明确说明。

## 在本地运行

涉及几何的一切都需要随 FreeCAD 附带的 Python 解释器,因为几何模块就在那里。把
`FREECAD_PYTHON` 指向它——不要在代码的任何位置硬编码平台路径。

```bash
python -m venv .venv
source .venv/bin/activate           # Windows: .venv\Scripts\activate
pip install -e .
pip install pytest

# Path to the Python interpreter bundled with your FreeCAD installation.
export FREECAD_PYTHON=/path/to/freecad/bin/python
# Windows: $env:FREECAD_PYTHON = "C:\path\to\freecad\bin\python.exe"

caddiff diff OLD.stp NEW.stp -o report/
```

在无显示器的机器上渲染需要虚拟显示:使用 Xvfb 或
`QT_QPA_PLATFORM=offscreen`;没有 GPU 时再加上 `LIBGL_ALWAYS_SOFTWARE=1`。

## 测试

```bash
pytest tests/unit
```

这些测试**不**需要 FreeCAD。一切会调用 FreeCAD 的部分都已被 mock,因此
`tests/unit` 在纯 Python 安装环境上就能运行,并且必须保持这一状态。如果某项
改动确实没有真实 FreeCAD 解释器就无法覆盖,请在 PR 中说明,并准确写出你手动
运行了什么、观察到了什么。"它应该能工作"不是验证结果。

## 提交之前:启用敏感数据闸门

`.gitignore` 只能拦截它已登记的文件名。对现实中真实会发生的那类事故——把
凭据、客户零件名或内部路径粘贴进一个看起来完全正常的文件——它无能为力。本项目
就真实发生过这种事,因此设置了一道自动化闸门:

```console
$ git config core.hooksPath .githooks   # once per clone
```

这会接好两个钩子:

| 钩子 | 覆盖范围 | 为什么是这个范围 |
|---|---|---|
| `pre-commit` | 暂存区中的文件 | 足够快,可以在每次提交时运行 |
| `pre-push` | 工作区**以及历史中的每一个 blob** | 一旦推送出去就收不回了,在后续提交里删掉那一行并不能把它从历史中移除 |

CI 会在完整克隆上运行同一检查,因此被跳过的钩子在 PR 阶段就会被捉到。这道闸门
是 **fail-closed** 的:如果找不到可用的 Python 解释器,它会拒绝提交,而不是静默
放行。`--no-verify` 是刻意留出的出路,并且会留下痕迹。

如果本地存在项目专属词表 `_internal/sensitive_terms.txt`,它也会对照该词表进行
检查。这份文件被刻意 gitignore——把客户名黑名单提交进公开仓库,其本身就会构成
泄露。设计说明见:`tools/scan_sensitive.py`。

随时可以手动运行:

```console
$ python tools/scan_sensitive.py            # worktree + full history
$ python tools/scan_sensitive.py --staged   # what a commit would add
```

## 代码风格

- **外科手术式改动。** 只改 issue 或 PR 所要求的内容。不要在同一次改动里顺手
  重构、重排格式或删除不相关的死代码。如果发现值得清理的内容,单独开一个 issue。
- 与你正在编辑的文件保持一致。PEP 8 是基线;保持行宽可读。
- 保持公开契约稳定:前面的退出码与 diff manifest 中的字段名是用户和 CI 所依赖的
  对象。新增字段没有问题;对字段改名或移除则属于破坏性变更。
- 不要静默失败。如果某一步被跳过、降级或回退到某个启发式,这一点必须体现在
  输出中,而不能只存在于日志里。
- 最终写进产物的文本(图片标注、报告正文、manifest 的 reason 字符串)都必须
  经过 i18n 表——见 `caddiff/i18n.py` 顶部的说明。`en` 与 `zh` 两种条目都要
  添加;只补其中一种的改动是不完整的。控制台进度输出直接写成英文,不经过该表。

## 提交信息

使用 [Conventional Commits](https://www.conventionalcommits.org/):

```
<type>(<scope>): <summary>
```

常用类型:`feat`、`fix`、`docs`、`test`、`refactor`、`perf`、`build`、`ci`、
`chore`。

```
feat(diff): report skipped parts in the manifest
fix(render): keep axis labels upright when the view is mirrored
docs: clarify the exit code contract
```

提交信息与代码遵守同样的数据规则:不得出现客户或产品名、主机名、token。

## Pull requests

- 让 PR 聚焦于单一事项。
- **附上一张你的改动实际产出的图片**——来自真实运行的真实渲染,不是示意图,
  也不是编辑器截图。
- 凡涉及几何或渲染的改动,请附上由同一输入对生成的**改动前/后**图片,让改动的
  效果清晰可见。
- 写明你运行的精确命令与观察到的退出码。
- 注明该 PR 所关闭的 issue。

## 硬性规则

这些不是风格偏好。违反其中任何一条的 PR 会被直接关闭。

1. **绝不提交客户或雇主的 CAD 数据。** 不要提交来自真实产品的 STEP/STP 文件,
   也不要提交带有真实零件名、客户名或客户目录名的衍生产物(PNG、JSON、HTML、
   PPTX)。样例数据必须是合成的——由仓库内的一个小脚本生成,或取自公开来源。
2. **绝不提交机密信息。** 不放 token、API key、密码、私有端点、服务器地址或
   IP——包括那些看起来像占位符、实际却是真实值的值。机密一旦进入 git 历史,
   即被视为已泄露。
3. **绝不提交 GPL 或 LGPL 二进制文件。** 属于仓库的只有构建配方(Dockerfile
   及其配套脚本)。FreeCAD、Open CASCADE Technology 及类似组件在镜像构建时安装。
4. **不要在二进制层面把本代码与 FreeCAD 或 OCCT 耦合。** 不做编译型 Python
   扩展,不做 `ctypes`/`dlopen` 绑定,不用承载复杂结构的共享内存。
   subprocess + 文件交换这条边界是刻意为之:正是它阻止 LGPL/GPL 组件传染到
   这份 Apache-2.0 代码库。参见 [THIRD_PARTY_LICENSES.md](THIRD_PARTY_LICENSES.md)。
5. **不做网络调用,不做遥测。** 该工具离线运行,不向任何地方发送任何内容。

## 贡献的许可

本项目采用 Apache License 2.0 授权。提交贡献即表示你同意该贡献按同一许可证
(inbound = outbound)提供,并确认你拥有提交它的权利。

## 疑问

如果本文件中有不清楚之处,或者你打算做的改动似乎与它相冲突,请先开一个 issue
询问,再动手写代码。我们宁可先回答一个问题,也不愿事后否掉一个大 PR。
