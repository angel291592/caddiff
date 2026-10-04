# Contributing to caddiff

Thanks for taking the time to contribute.

`caddiff` is `git diff for CAD assemblies`: you give it two versions of a STEP/STP
assembly and it gives you back a diff highlight image, a human-readable diff
report (HTML/Markdown), and a machine-readable manifest (JSON).

Two companion documents matter as much as this one:

- [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md) - the behaviour expected in every
  project space.
- [SECURITY.md](SECURITY.md) - how to report a vulnerability privately. Never
  open a public issue for a security problem.

## Ways to contribute

- Report a bug with a minimal reproduction (see the data rules below - never
  attach a real customer model).
- Improve the diff quality: part pairing, change classification, rendering.
- Improve the docs, including this file.
- Add tests. Small, focused, offline tests are the most valuable contribution
  we can get.

## Run it with Docker (one command)

The published image already contains FreeCAD, so this is the shortest path:

```bash
docker run --rm -v "$PWD:/work" ghcr.io/<owner>/<repo>:latest diff OLD.stp NEW.stp -o report/
```

On Windows PowerShell:

```powershell
docker run --rm -v ${PWD}:/work ghcr.io/<owner>/<repo>:latest diff OLD.stp NEW.stp -o report/
```

The exit code is part of the public contract:

| Exit code | Meaning |
|---|---|
| `0` | No differences. |
| `1` | Differences detected. |
| `2` | Execution failed (unreadable input, timeout, crashed subprocess, ...). |

If you change anything that can affect these codes, say so explicitly in your
pull request.

## Run it locally

Anything that touches geometry needs the Python interpreter that ships with
FreeCAD, because that is where the geometry modules live. Point `FREECAD_PYTHON`
at it - do not hardcode a platform path anywhere in the code.

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

Rendering on a headless machine needs a virtual display: use Xvfb or
`QT_QPA_PLATFORM=offscreen`, and `LIBGL_ALWAYS_SOFTWARE=1` when there is no GPU.

## Tests

```bash
pytest tests/unit
```

These tests do **not** need FreeCAD. Everything that would call out to FreeCAD is
mocked, so `tests/unit` runs on a plain Python installation and must stay that
way. If a change genuinely cannot be covered without a real FreeCAD interpreter,
say so in the pull request and describe exactly what you ran by hand and what you
observed. "It should work" is not a verification result.

## Before you commit: enable the sensitive-data gate

`.gitignore` only blocks file names it already knows about. It does nothing about the
realistic accident: pasting a credential, a customer part name, or an internal path into a
perfectly normal-looking file. This project has had exactly that happen, so there is an
automated gate:

```console
$ git config core.hooksPath .githooks   # once per clone
```

That wires up two hooks:

| Hook | Scope | Why that scope |
|---|---|---|
| `pre-commit` | the staged files | fast enough to run on every commit |
| `pre-push` | the worktree **and every blob in the history** | after a push it is out, and deleting the line in a later commit does not take it out of the history |

CI runs the same check over a full clone, so a skipped hook is caught at the PR. The gate
is **fail-closed**: if it cannot find a working Python interpreter it refuses the commit
rather than passing silently. `--no-verify` is the deliberate way out, and it leaves a
trace.

It will also check against a project-specific term list if one is present locally at
`_internal/sensitive_terms.txt`. That file is gitignored on purpose — a blocklist of
customer names committed to a public repository would itself be the leak. Design notes:
`tools/scan_sensitive.py`.

Run it by hand at any time:

```console
$ python tools/scan_sensitive.py            # worktree + full history
$ python tools/scan_sensitive.py --staged   # what a commit would add
```

## Code style

- **Surgical changes.** Change only what the issue or pull request requires. Do
  not refactor, reformat, or delete unrelated dead code in the same change. If
  you spot something worth cleaning up, open a separate issue.
- Match the file you are editing. PEP 8 is the baseline; keep lines readable.
- Keep the public contract stable: the exit codes above and the field names in
  the diff manifest are what users and CI depend on. Adding a field is fine;
  renaming or removing one is a breaking change.
- Do not fail silently. If a step is skipped, degraded, or falls back to a
  heuristic, that has to be visible in the output, not just in the log.
- Text that ends up inside an artifact (image captions, report body, manifest
  reason strings) goes through the i18n table - see the notes at the top of
  `caddiff/i18n.py`. Add both the `en` and the `zh` entry; a change with only one
  of them is incomplete. Console progress output is plain English and does not
  go through the table.

## Commit messages

Use [Conventional Commits](https://www.conventionalcommits.org/):

```
<type>(<scope>): <summary>
```

Common types: `feat`, `fix`, `docs`, `test`, `refactor`, `perf`, `build`, `ci`,
`chore`.

```
feat(diff): report skipped parts in the manifest
fix(render): keep axis labels upright when the view is mirrored
docs: clarify the exit code contract
```

Commit messages are subject to the same data rules as the code: no customer or
product names, no hostnames, no tokens.

## Pull requests

- Keep the pull request focused on one thing.
- **Attach one image that your change actually produced** - a real render from a
  real run, not a mockup and not a screenshot of your editor.
- For anything touching geometry or rendering, attach **before/after** images
  produced from the same input pair, so the effect of the change is visible.
- State the exact command you ran and the exit code you observed.
- Reference the issue the pull request closes.

## Hard rules

These are not style preferences. A pull request that breaks one of them will be
closed.

1. **Never commit customer or employer CAD data.** No STEP/STP files from a real
   product, and no derived artifacts (PNG, JSON, HTML, PPTX) that carry real part
   names, customer names, or customer directory names. Example data must be
   synthetic - generated by a small script in the repository, or taken from a
   public source.
2. **Never commit secrets.** No tokens, API keys, passwords, private endpoints,
   server addresses, or IPs - including values that look like placeholders but
   are real. Once a secret reaches git history it is considered leaked.
3. **Never commit GPL or LGPL binaries.** Only the build recipe (the Dockerfile
   and its supporting scripts) belongs in the repository. FreeCAD, Open CASCADE
   Technology and similar components are installed at image build time.
4. **Do not couple this code to FreeCAD or OCCT at the binary level.** No
   compiled Python extension, no `ctypes`/`dlopen` binding, no shared memory
   carrying complex structures. The subprocess plus file-exchange boundary is
   deliberate: it is what keeps the LGPL/GPL components from propagating into
   this Apache-2.0 codebase. See [THIRD_PARTY_LICENSES.md](THIRD_PARTY_LICENSES.md).
5. **No network calls, no telemetry.** The tool runs offline and does not send
   anything anywhere.

## Licensing of contributions

This project is licensed under the Apache License 2.0. By submitting a
contribution you agree that it is provided under the same license (inbound =
outbound), and you confirm that you have the right to submit it.

## Questions

If something in this document is unclear, or if a change you want to make seems
to conflict with it, open an issue and ask before writing the code. We would
rather answer a question up front than reject a large pull request afterwards.
