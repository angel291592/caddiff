"""脱敏闸门 —— 阻止凭据、客户数据与内部信息进入本仓库或它的 git 历史。

## 为什么"敏感词表"不能写进本文件

本文件是**公开仓**的一部分。若把客户型号 / 内网 IP / 域名硬编码进来当黑名单，这份黑名单
本身就变成了泄露物——闸门反倒成了泄露源。所以本文件**只放与具体客户无关的通用规则**，
客户专属词表放在被 gitignore 的 `_internal/sensitive_terms.txt`，由本脚本在本机自动加载。

## 两层闸门

| 层 | 规则来源 | 生效范围 |
|---|---|---|
| **通用层** | 本文件 | 任何克隆、任何 CI —— 凭据形态、禁止入库的文件类型、本机绝对路径、高熵串 |
| **专属层** | `_internal/sensitive_terms.txt`（永不入库） | 只在本机 —— 客户型号、内网 IP、域名、调用方名 |

公开仓里本来就不该出现专属层那些词，所以 CI 不需要它；而作者本机**必须**有它，
否则「不小心粘了一段客户型号」这类事故没有任何拦截。

## 为什么不回显命中的明文

命中内容可能就是那把钥匙。打印它等于把泄露从文件搬到终端、CI 日志、聊天记录里。
本脚本只报 `文件:行号 + 规则名`；专属层报 `词条编号`（作者自己查表即可）。

## 用法

    python tools/scan_sensitive.py            # 审计：工作区 + 全部 git 历史
    python tools/scan_sensitive.py --staged   # 只扫暂存区（pre-commit 钩子用）
    python tools/scan_sensitive.py --quiet    # 只输出结论

退出码：`0` 通过 / `1` 命中 / `2` 环境问题（不是 git 仓库等）。
"""
import argparse
import os
import re
import subprocess
import sys

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# ── 通用层规则 ──────────────────────────────────────────────────────────────
# 每条：规则名 -> 正则。正则在**文本行**上匹配。
# ⚠️ 新增规则后必须用 `python tools/scan_sensitive.py` 自查本文件是否自命中。
TEXT_RULES = {
    # 硬编码的凭据赋值。故意排除 ${VAR:-} / os.environ.get(...) 这类取值写法——
    # 它们是正确做法，不该被拦。
    "credential-literal": re.compile(
        r"""(?i)\b(?:token|secret|passwd|password|api[_-]?key|access[_-]?key|"""
        r"""app[_-]?secret|client[_-]?secret)\b\s*[:=]\s*["'][^"'$\{\s]{12,}["']"""),
    "openai-style-key": re.compile(r"\bsk-[A-Za-z0-9]{16,}\b"),
    "github-token": re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}\b"),
    "aws-access-key": re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    "slack-token": re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{10,}\b"),
    "private-key-block": re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    # 高熵十六进制串（40 位起）。跳过校验和 / data URI 这类常见良性上下文。
    "long-hex-string": re.compile(r"\b[0-9a-fA-F]{40,}\b"),
    # 本机绝对路径：暴露用户名与目录结构，且换机器必然失效。
    "absolute-user-path": re.compile(r"(?i)\b[a-z]:\\\\?users\\\\?|\b[a-z]:/users/|/home/[a-z0-9._-]+/"),
}

# 良性上下文：命中这些词的行不报。
# why 只豁免下面两类规则：它们是**形态类**判据（"看起来像密钥"），文档里的占位符
# 与校验和天然长得一样，不豁免就会满屏误报——而会误报的闸门会被关掉，那才是真风险。
# `sk-` / `ghp_` / `AKIA` 这类**前缀明确**的规则不豁免：它们误报率本来就低。
_BENIGN_CONTEXT = re.compile(
    r"(?i)sha256|sha1|sha512|\bmd5\b|checksum|data:image|base64|example|placeholder|"
    r"dummy|redacted|masked|your[-_]|<your|xxxx|已脱敏|示例")
_BENIGN_EXEMPT = frozenset({"credential-literal", "long-hex-string"})

# ── 禁止入库的文件（按路径/扩展名）─────────────────────────────────────────
# (正则, 规则名, 允许的例外目录)
FORBIDDEN_PATHS = [
    (re.compile(r"(?i)\.(pptx|ppt|docx|xlsx)$"), "office-artifact", ()),
    (re.compile(r"(?i)\.(stp|step|stl|brep|3mf|iges|igs)$"), "cad-data", ("examples/fixtures/",)),
    (re.compile(r"(?i)\.(png|jpg|jpeg|gif|webp)$"), "image-artifact",
     ("docs/images/", "examples/expected/images/")),
    (re.compile(r"(?i)(^|/)\.env($|\.)"), "dotenv", ()),
    (re.compile(r"(?i)\.(key|pem|p12|pfx|jks|keystore)$"), "key-material", ()),
    (re.compile(r"(?i)\.(tgz|tar\.gz|zip|7z|rar)$"), "archive", ()),
    (re.compile(r"(?i)(^|/)(id_rsa|id_ed25519|\.netrc|\.npmrc|\.pypirc|credentials)$"),
     "credential-file", ()),
    (re.compile(r"(?i)(^|/)(_internal|_probe_demo|tests/_scratch)/"), "internal-dir", ()),
]

_MAX_BLOB_BYTES = 4 * 1024 * 1024


def _run_git(args):
    return subprocess.run(["git", "-C", REPO_ROOT] + args,
                          capture_output=True, text=True, encoding="utf-8",
                          errors="replace")


def _is_binary(data: bytes) -> bool:
    return b"\x00" in data[:8192]


def load_sensitive_terms():
    """加载专属层词表。文件不存在时返回空表（CI / 他人克隆的正常情况）。"""
    candidates = []
    env = os.environ.get("CADDIFF_SENSITIVE_TERMS")
    if env:
        candidates.append(env)
    candidates.append(os.path.join(REPO_ROOT, "_internal", "sensitive_terms.txt"))
    for path in candidates:
        if not os.path.isfile(path):
            continue
        terms = []
        with open(path, "r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if line and not line.startswith("#"):
                    terms.append(line)
        return terms
    return []


def scan_text(text, label, terms):
    """扫一段文本，返回 [ (label, 行号, 规则名/词条编号) ]。"""
    findings = []
    for lineno, line in enumerate(text.splitlines(), 1):
        benign = bool(_BENIGN_CONTEXT.search(line))
        for name, rx in TEXT_RULES.items():
            if benign and name in _BENIGN_EXEMPT:
                continue
            if rx.search(line):
                findings.append((label, lineno, name))
        low = line.lower()
        for idx, term in enumerate(terms, 1):
            if term.lower() in low:
                findings.append((label, lineno, f"sensitive-term #{idx}"))
    return findings


def check_path(path):
    """按路径规则检查单个文件路径。"""
    findings = []
    norm = path.replace("\\", "/").lstrip("./")
    for rx, name, allow in FORBIDDEN_PATHS:
        if not rx.search(norm):
            continue
        if any(norm.startswith(a) or f"/{a}" in f"/{norm}" for a in allow):
            continue
        findings.append((norm, 0, name))
    return findings


def iter_worktree_files():
    """只扫 **git 会考虑的文件**：已跟踪 + 未跟踪且未被忽略。

    why 不直接 ``os.walk``：那会把 `_internal/`（被 gitignore 的内部资料）也算进来，
    闸门就会对自己人误报。判定「能不能进库」的标准必须与 git 一致，而不是另写一套
    ——两套标准必然漂移，而漂移的方向通常是「闸门以为安全、git 却收进去了」。
    """
    out = _run_git(["ls-files", "-z", "--cached", "--others", "--exclude-standard"])
    for rel in out.stdout.split("\0"):
        rel = rel.strip()
        if not rel:
            continue
        yield rel.replace("\\", "/"), os.path.join(REPO_ROOT, rel)


def scan_staged(terms):
    findings = []
    out = _run_git(["diff", "--cached", "--name-only", "--diff-filter=ACMR"])
    for path in [p for p in out.stdout.splitlines() if p.strip()]:
        findings += check_path(path)
        blob = _run_git(["show", f":{path}"])
        if blob.returncode != 0:
            continue
        data = blob.stdout.encode("utf-8", "replace")
        if _is_binary(data):
            continue
        findings += scan_text(blob.stdout, f"staged:{path}", terms)
    return findings


def scan_worktree(terms):
    findings = []
    for rel, full in iter_worktree_files():
        findings += check_path(rel)
        try:
            with open(full, "rb") as fh:
                data = fh.read(_MAX_BLOB_BYTES)
        except OSError:
            continue
        if _is_binary(data):
            continue
        findings += scan_text(data.decode("utf-8", "replace"), f"worktree:{rel}", terms)
    return findings


def scan_history(terms):
    """扫全部提交里出现过的每个 **blob**（跳过 tree 与 commit）。

    why 必须按类型过滤：``git cat-file -p <tree>`` 输出的是
    ``100644 blob <40 位十六进制 sha>\\t文件名``——那些 sha 会被高熵规则**全部**误报。
    第一版实现就踩了这个坑：178 条命中里绝大多数是 tree 的内容，真信号被彻底淹掉。
    """
    findings = []
    out = _run_git(["rev-list", "--objects", "--all"])
    if out.returncode != 0:
        return findings

    entries = []
    for line in out.stdout.splitlines():
        sha, _, path = line.partition(" ")
        if sha:
            entries.append((sha, path))
    if not entries:
        return findings

    # 一次 batch-check 拿到全部对象类型，避免为每个对象起一个 git 进程
    proc = subprocess.run(
        ["git", "-C", REPO_ROOT, "cat-file", "--batch-check=%(objectname) %(objecttype)"],
        input="\n".join(sha for sha, _ in entries),
        capture_output=True, text=True, encoding="utf-8", errors="replace")
    types = {}
    for line in proc.stdout.splitlines():
        parts = line.split()
        if len(parts) == 2:
            types[parts[0]] = parts[1]

    seen = set()
    for sha, path in entries:
        if types.get(sha) != "blob" or sha in seen:
            continue
        seen.add(sha)
        if path:
            findings += check_path(path)
        blob = _run_git(["cat-file", "-p", sha])
        if blob.returncode != 0:
            continue
        data = blob.stdout.encode("utf-8", "replace")
        if _is_binary(data) or len(data) > _MAX_BLOB_BYTES:
            continue
        findings += scan_text(blob.stdout, f"history:{path or sha[:8]}", terms)
    return findings


def main(argv=None):
    ap = argparse.ArgumentParser(description="Sensitive-data gate for this repository")
    ap.add_argument("--staged", action="store_true",
                    help="scan only the git index (for the pre-commit hook)")
    ap.add_argument("--quiet", action="store_true", help="print the verdict only")
    args = ap.parse_args(argv)

    if _run_git(["rev-parse", "--git-dir"]).returncode != 0:
        print("ERROR: not a git repository", file=sys.stderr)
        return 2

    terms = load_sensitive_terms()
    if args.staged:
        findings, scope = scan_staged(terms), "staged"
    else:
        findings = scan_worktree(terms) + scan_history(terms)
        scope = "worktree + full git history"

    if not findings:
        if not args.quiet:
            print(f"sensitive-data gate: PASS ({scope})"
                  + (f" — {len(terms)} project-specific terms loaded" if terms else
                     " — no project-specific term list found (fine on CI)"))
        return 0

    print(f"sensitive-data gate: FAIL ({scope}) — {len(findings)} finding(s)\n")
    # 只报位置与规则名：命中内容本身可能就是那把钥匙，不能回显。
    for path, lineno, rule in sorted(set(findings)):
        where = f"{path}:{lineno}" if lineno else path
        print(f"  {where}  [{rule}]")
    print("\nFix the file, then re-run.")
    print("If it is already committed, a rewrite is the only way to take it out of history:")
    print("    git filter-repo --replace-text expressions.txt   # or --path <file> --invert-paths")
    print("…and rotate the credential regardless: once it reached a commit it is compromised,")
    print("even after the history no longer shows it.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
