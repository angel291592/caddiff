"""caddiff 命令行入口 —— 退出码契约的唯一实现处。

## 退出码（对外承诺，可直接当 CI 闸门）

| 码 | 含义 |
|---|---|
| `0` | 两版**无差异** |
| `1` | **检出差异** |
| `2` | **执行失败**（输入不可读 / 子进程超时或崩溃 / 内部错误 / 参数错误） |

这条契约的难点全在「1 与 2 的区分」：早先编排层任何一步失败都 `sys.exit(1)`，
于是「FreeCAD 崩了」和「两版真的有差异」在 CI 里长得一模一样——闸门会放行本该拦下的
提交，也会拦住本该放行的提交。现在失败一律抛 `PipelineError`，由这里翻译成 2。

## 为什么 argparse 报错也用 2

argparse 自身 `sys.exit(2)`，与本契约一致，不需要拦截。

## `difftool` 子命令为什么要单独存在

`git difftool` 把工具的非零退出码视为「external diff died」并中断整个 diff。
而 `diff` 在检出差异时**必须**返回 1。所以给 git 用的路径单独开一个子命令：
它只在**执行失败**时返回 2，检出差异照旧返回 0。
"""
import argparse
import os
import sys
import tempfile

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
if SCRIPT_DIR not in sys.path:
    sys.path.insert(0, SCRIPT_DIR)

import console  # noqa: E402
import i18n  # noqa: E402
import run_pipeline  # noqa: E402
from version import __version__  # noqa: E402

console.enable_utf8_output()

EXIT_OK = run_pipeline.EXIT_OK
EXIT_DIFF = run_pipeline.EXIT_DIFF
EXIT_ERROR = run_pipeline.EXIT_ERROR

DEFAULT_OUT = "caddiff-out"


def _add_diff_args(parser, *, with_out=True, out_default=DEFAULT_OUT):
    """`diff` 与 `difftool` 共用的参数集——两处各写一遍必然漂移。"""
    parser.add_argument("old", help="old version STEP/STP file")
    parser.add_argument("new", help="new version STEP/STP file")
    if with_out:
        parser.add_argument("-o", "--out", default=out_default, metavar="DIR",
                            help=f"output directory (default: {out_default})")
    parser.add_argument("--label-old", default=None,
                        help="label for the old version (default: derived from filename)")
    parser.add_argument("--label-new", default=None,
                        help="label for the new version (default: derived from filename)")
    parser.add_argument("--lang", default=None, choices=i18n.available_langs(),
                        help="language of the generated report text "
                             "(default: $CADDIFF_LANG, else en)")
    parser.add_argument("--pptx", action="store_true",
                        help="also export the comparison as a PowerPoint deck")
    parser.add_argument("--max-faces", type=int, default=None,
                        help="skip boolean ops for parts with more faces than this (default: 5000)")
    parser.add_argument("--boolean-timeout", type=float, default=None,
                        help="hard timeout in seconds for a single boolean op (default: 60)")
    parser.add_argument("--render-timeout", type=float, default=None,
                        help="override the render step deadline in seconds. By default it "
                             "is estimated from the number of differences "
                             "(180 + 60 per change); raise it for large assemblies whose "
                             "rendering legitimately takes longer, otherwise the step is "
                             "killed and the run fails with exit code 2")
    parser.add_argument("--skip-parts", default=None, metavar="A,B",
                        help="comma-separated base_names to skip")
    parser.add_argument("--min-diff-pct", type=float, default=None,
                        help="only report diffs larger than this %% of the part volume (default: 0)")
    return parser


def build_parser():
    ap = argparse.ArgumentParser(
        prog="caddiff",
        description="git diff for CAD assemblies — compare two STEP/STP assemblies "
                    "and show what changed",
        epilog="Exit codes: 0 = no differences, 1 = differences found, 2 = failure.")
    ap.add_argument("--version", action="version", version=f"caddiff {__version__}")
    sub = ap.add_subparsers(dest="command", metavar="<command>", required=True)

    d = sub.add_parser("diff", help="compare two assemblies and write a report",
                       description="Compare two STEP/STP assemblies and write a report.")
    _add_diff_args(d)

    dt = sub.add_parser("difftool",
                        help="git difftool driver: always exit 0 unless the run failed",
                        description="Same as `diff`, but exits 0 when differences are "
                                    "found, so `git difftool` does not abort.")
    _add_diff_args(dt, with_out=False)
    dt.add_argument("-o", "--out", default=None, metavar="DIR",
                    help="output directory (default: a fresh temp directory)")

    return ap


def _run_diff_args(args, output_dir):
    """把解析好的参数交给编排层。返回 (manifest, exit_code)。"""
    manifest = run_pipeline.run_diff(
        args.old, args.new, output_dir,
        label_old=args.label_old, label_new=args.label_new, lang=args.lang,
        max_faces=args.max_faces, boolean_timeout=args.boolean_timeout,
        skip_parts=args.skip_parts, min_diff_pct=args.min_diff_pct,
        render_timeout=args.render_timeout,
        export_pptx=args.pptx)
    return manifest


def cmd_diff(args):
    try:
        manifest = _run_diff_args(args, args.out)
    except run_pipeline.PipelineError as exc:
        print(f"\nERROR: {exc}", file=sys.stderr)
        return EXIT_ERROR
    return EXIT_DIFF if manifest["summary"]["has_differences"] else EXIT_OK


def cmd_difftool(args):
    """git difftool 驱动：检出差异不是错误，只有执行失败才是。"""
    out_dir = args.out
    temp_created = False
    if not out_dir:
        out_dir = tempfile.mkdtemp(prefix="caddiff-difftool-")
        temp_created = True
    try:
        manifest = _run_diff_args(args, out_dir)
    except run_pipeline.PipelineError as exc:
        print(f"\nERROR: {exc}", file=sys.stderr)
        return EXIT_ERROR
    n = manifest["summary"]["geometry_diff_count"] + manifest["summary"]["bom_diff_count"]
    print(f"\nReport: {os.path.join(out_dir, 'diff_manifest.json')}")
    if n:
        print(f"{n} difference(s) found — see report.html in the same directory.")
    else:
        print("No differences detected.")
    if temp_created:
        print(f"(output kept in {out_dir})")
    return EXIT_OK


def main(argv=None):
    args = build_parser().parse_args(argv)
    if args.command == "difftool":
        return cmd_difftool(args)
    return cmd_diff(args)


if __name__ == "__main__":
    sys.exit(main())
