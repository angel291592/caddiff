# 在 `git diff` / `git difftool` 中使用 caddiff

[English](git-integration.md) | 简体中文

项目名 `git diff for CAD assemblies` 的全部意义就在于：下面这条命令应当真的能工作：

```console
$ git difftool -t caddiff HEAD~1 -- bracket.stp
```

本页给出让那句话成立的配置。

---

## 1. 为什么 CAD 文件在 git 里需要特殊处理

STEP/STL 是**纯文本**，所以 git 会把它们当作文本处理：尝试按行 diff、按行合并。对几何来说，这两种做法都是错的：

- **Diff 噪声。** 一个 20 KB 的 STEP 文件就是 ~4,000 行坐标。只改一个零件，就会产生几千行变更，却看不出*究竟改了什么*。
- **静默的坏合并。** 对两个 STEP 文件按行合并，会产出一个语法上有效、**几何上却是错的**文件。不会有任何警告，也没人会去审查 40,000 行坐标。

所以：把 CAD 文件标记为二进制（本仓库附带了一个做这件事的 `.gitattributes`），并改用真正的几何比较。

---

## 2. 配置 `git difftool`

`caddiff` 正是为此随包提供了一个专用的 `difftool` 子命令。它与 `caddiff diff` 只有一个关键差别：**发现差异时以 0 退出**。`git difftool` 会把工具的非零退出码视作 "external diff died"（外部 diff 崩溃），并中止整个运行——所以 `caddiff diff`（为 CI 起见，它必须在有差异时返回 1）在这里是错误的命令。

```console
# register the tool
$ git config --global difftool.caddiff.cmd 'caddiff difftool "$LOCAL" "$REMOTE"'

# don't ask for confirmation on every file
$ git config --global difftool.prompt false
```

然后：

```console
# compare a single file across the last commit
$ git difftool -t caddiff HEAD~1 -- bracket.stp

# compare a whole branch
$ git difftool -t caddiff main -- '*.stp'

# compare a commit range
$ git difftool -t caddiff v1.2..v1.3 -- assemblies/
```

每次调用都会写入一份全新的报告，并打印它的位置：

```
Report: /tmp/caddiff-difftool-a1b2c3/diff_manifest.json
3 difference(s) found — see report.html in the same directory.
```

打开打印出来的 `report.html` 即可查看高亮图片和变更清单。

---

## 3. 让原生 `git diff` 也委托出去（可选）

如果你想让 `git diff` 本身就把 STEP 文件路由给 caddiff，请取消 `.gitattributes` 中 `diff=caddiff` 各行的注释，并定义该 driver：

```console
$ git config --global diff.caddiff.command 'caddiff difftool "$LOCAL" "$REMOTE"'
```

注意：这会为 diff 中的**每一个** CAD 文件运行一次 caddiff，而在真实装配体上，单次运行要花几十秒（渲染占大头——参见 README 中的基准表）。在一个动到 20 个零件的分支上，那就得等上喝杯咖啡的功夫。按需、逐文件调用的 `git difftool` 通常是更好的取舍。

---

## 4. 选择比较哪些修订版本

`git difftool` 会把 git 的“改动前”文件作为 `$LOCAL`、“改动后”文件作为 `$REMOTE` 传入。要控制这两个具体是哪些修订版本，使用常规的 git 修订版本语法：

| 目标 | 命令 |
|---|---|
| 工作树 vs 最近一次提交 | `git difftool -t caddiff -- model.stp` |
| 最近一次提交 vs 上一次提交 | `git difftool -t caddiff HEAD~1 -- model.stp` |
| 两个分支 | `git difftool -t caddiff main..feature -- model.stp` |
| 两个 tag | `git difftool -t caddiff v1.0..v2.0 -- model.stp` |
| 任意两个修订版本 | `git difftool -t caddiff <rev1> <rev2> -- model.stp` |

生成图片上的标签取自 git 使用的文件名（都是临时文件名，因此没有参考价值）。请自行传入 `--label-old` / `--label-new`，即可让图片标出 `v1.2` / `v1.3`：

```console
$ git config --global difftool.caddiff.cmd \
    'caddiff difftool "$LOCAL" "$REMOTE" --label-old v1.2 --label-new v1.3'
```

---

## 5. CI：使用 `caddiff diff`，而不是 `caddiff difftool`

在 CI 里你要的*恰恰是*非零退出码——它就是闸门：

| 退出码 | 含义 |
|---|---|
| `0` | 无差异 |
| `1` | 发现差异 |
| `2` | 执行失败（输入无效、超时、崩溃） |

```yaml
- run: |
    docker run --rm -v "$PWD:/data" ghcr.io/angel291592/caddiff:v0.3.4 \
      diff /data/old.stp /data/new.stp -o /data/report
  # exit 1 fails the step, which is what you want on a PR that changes geometry
```

注意：`caddiff difftool` 即使在有差异时也返回 `0`，因此它绝**不**能用作 CI 闸门。
