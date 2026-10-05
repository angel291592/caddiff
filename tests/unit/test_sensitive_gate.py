"""脱敏闸门自身的回归测试。

为什么闸门必须有测试：**一个不响的闸门比没有闸门更危险**——它给人「已经防住了」的错觉。
这里逐条钉住「哪类东西会被拦」与「哪类不该误报」，以及两条自证：

* 闸门的源码不许自命中（否则每次提交都被自己拦下，然后被人 `--no-verify` 绕过）；
* **本测试文件本身**也不许自命中——所以下面所有"违规样本"都在**运行时拼装**，
  源码里不留任何真字面量。这条约束由最后一个测试自动守住。

不联网、不依赖 FreeCAD、导入时零副作用（AGENTS.md §7）。
"""
import os
import subprocess

import scan_sensitive as gate

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.abspath(os.path.join(_HERE, "..", ".."))
_GATE_SRC = os.path.join(_HERE, "..", "..", "tools", "scan_sensitive.py")

# ── 违规样本：运行时拼装，源码里不留字面量 ────────────────────────────────
_HEX = "0123456789abcdef"
_HEX40 = _HEX * 2 + "01234567"                    # 40 位十六进制
_UUIDISH = "a925d25a" + "-f5a7-4968-b2a0-5924e877aba2"
_SK_KEY = "sk-" + "abcdefghijklmnopqrstuvwx"
_GH_TOKEN = "ghp_" + "A" * 30
_AWS_KEY = "AKIA" + "IOSFODNN7EXAMPLX"
_PRIVATE_KEY_BLOCK = "-" * 5 + "BEGIN RSA PRIVATE KEY" + "-" * 5
_WIN_PATH = "C:" + chr(92) + "Users" + chr(92) + "Administrator" + chr(92) + "project"
_NIX_PATH = "/home/" + "deploy/cad/data.stp"


def _rules(text):
    return {rule for _, _, rule in gate.scan_text(text, "t", [])}


# ── 会被拦 ────────────────────────────────────────────────────────────────

def test_hardcoded_token_literal_is_caught():
    # 这正是本项目历史上真实发生过的事故形态
    assert "credential-literal" in _rules(f'TOKEN="{_HEX40}"')


def test_hardcoded_api_key_assignment_is_caught():
    assert "credential-literal" in _rules(f'api_key: "{_UUIDISH}"')


def test_known_key_prefixes_are_caught():
    assert "openai-style-key" in _rules(f'KEY = "{_SK_KEY}"')
    assert "github-token" in _rules(_GH_TOKEN)
    assert "aws-access-key" in _rules(_AWS_KEY)


def test_private_key_block_is_caught():
    assert "private-key-block" in _rules(_PRIVATE_KEY_BLOCK)


def test_long_hex_secret_is_caught():
    assert "long-hex-string" in _rules("blob = " + _HEX * 3)


def test_absolute_user_path_is_caught():
    assert "absolute-user-path" in _rules("root = " + _WIN_PATH)
    assert "absolute-user-path" in _rules("path = " + _NIX_PATH)


def test_sensitive_terms_are_reported_by_index_not_by_text():
    findings = gate.scan_text("part ACME-PART-9 measured", "t", ["ACME-PART-9", "OTHER"])
    assert findings == [("t", 1, "sensitive-term #1")]


# ── 不该误报（误报会让闸门被关掉，那才是真风险）──────────────────────────

def test_env_var_interpolation_is_not_a_secret():
    assert not _rules('TOKEN="${STP_API_TOKEN:?}"')
    assert not _rules("API_KEY=${DEEPSEEK_API_KEY:-}")
    assert not _rules('token = os.environ.get("STP_API_TOKEN", "")')


def test_checksums_and_data_uris_are_not_secrets():
    assert "long-hex-string" not in _rules("sha256: " + "a" * 64)
    assert "long-hex-string" not in _rules("data:image/png;base64," + "b" * 64)


def _head_sha():
    out = subprocess.run(["git", "-C", _ROOT, "rev-parse", "HEAD"],
                         capture_output=True, text=True, encoding="utf-8")
    return out.stdout.strip()


def test_this_repositorys_own_commit_ids_are_not_secrets():
    # release-please 把每条提交写成 /commit/<40 位 sha> 链接，CI 扫全 ref 时会读到它。
    # 误报的代价是 main 永久红灯（每次发布 PR 都重建该分支），所以这条必须放行。
    head = _head_sha()
    assert len(head) == 40, "expected a full hex object name, got %r" % head
    assert head in gate.repo_object_names()
    assert "long-hex-string" not in _rules(f"* fix something ([1a2b3c4](http://x/commit/{head}))")


def test_a_40_hex_string_that_is_not_an_object_is_still_caught():
    # 放行的判据是「本仓库的对象名」，不是「长得像 commit id」。
    assert "long-hex-string" in _rules("blob = " + _HEX40)


def test_the_object_exemption_is_per_match_not_per_line():
    # 安全属性：同一行里，一个自家 commit id 不许把旁边的真密钥一起豁免掉。
    # 这正是 _BENIGN_CONTEXT 那种行级豁免做不到的事，所以这里必须是匹配级。
    line = f"see http://x/commit/{_head_sha()} then blob = {_HEX40}"
    assert "long-hex-string" in _rules(line)


def test_placeholder_values_are_not_secrets():
    assert "credential-literal" not in _rules('api_key = "your-key-here"')
    assert "credential-literal" not in _rules('token = "<redacted>"')


# ── 路径规则 ──────────────────────────────────────────────────────────────

def test_forbidden_paths_are_caught():
    assert {r for _, _, r in gate.check_path("out/compare.pptx")} == {"office-artifact"}
    assert {r for _, _, r in gate.check_path("vendor/part.step")} == {"cad-data"}
    assert {r for _, _, r in gate.check_path("report/images/a.png")} == {"image-artifact"}
    assert {r for _, _, r in gate.check_path("deploy/.env")} == {"dotenv"}
    assert {r for _, _, r in gate.check_path("keys/server.pem")} == {"key-material"}
    assert {r for _, _, r in gate.check_path("dump/export.tgz")} == {"archive"}
    assert {r for _, _, r in gate.check_path("_internal/notes.md")} == {"internal-dir"}
    assert {r for _, _, r in gate.check_path("tests/_scratch/verify.py")} == {"internal-dir"}
    assert {r for _, _, r in gate.check_path(".intent/run.intent.yaml")} == {"internal-dir"}
    # 本机专属文件：按名字拦，内容规则扫不出来（既无密钥也无专属词表命中项）
    assert {r for _, _, r in gate.check_path("AGENTS.md")} == {"local-only-file"}
    assert {r for _, _, r in gate.check_path("nested/AGENTS.md")} == {"local-only-file"}
    # 大小写：Windows/macOS 上文件名大小写不敏感，规则必须同样不敏感
    assert {r for _, _, r in gate.check_path("agents.md")} == {"local-only-file"}


def test_local_only_rule_does_not_overreach():
    """不能误伤合法文件——闸门误报多了就会被关掉，那比漏报更糟。"""
    for path in ("docs/AGENTS-public.md",      # 前缀相同但不是它
                 "AGENTS.md.example",          # 扩展名不同
                 "docs/agents-guide.md",       # 只是名字里有 agents
                 "README.md"):
        assert gate.check_path(path) == [], path


def test_allowed_paths_are_not_flagged():
    for path in ("examples/fixtures/moved_old.stp",
                 "docs/images/hero.png",
                 "examples/expected/images/BRACKET_0_overview_rect.png",
                 "caddiff/render_diff.py",
                 "tools/scan_sensitive.py"):
        assert gate.check_path(path) == [], path


# ── 自证：闸门与它的测试都不许自命中 ──────────────────────────────────────

def _self_scan(path):
    with open(path, "r", encoding="utf-8") as fh:
        return gate.scan_text(fh.read(), os.path.basename(path), [])


def test_gate_source_does_not_trip_its_own_rules():
    assert _self_scan(_GATE_SRC) == []


def test_this_test_file_does_not_trip_the_gate():
    # 这条是结构性守卫：只要有人在本文件里写下一个真字面量，它立刻红。
    assert _self_scan(os.path.abspath(__file__)) == []
