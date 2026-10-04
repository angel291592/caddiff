# Using caddiff with `git diff` / `git difftool`

The whole point of the name `git diff for CAD assemblies` is that this should work:

```console
$ git difftool -t caddiff HEAD~1 -- bracket.stp
```

This page is the setup that makes that sentence true.

---

## 1. Why CAD files need special handling in git

STEP/STL are **plain text**, so git treats them as text: it tries line-level diffs and
line-level merges. Both are wrong for geometry:

- **Diff noise.** A 20 KB STEP file is ~4,000 lines of coordinates. A one-part change
  shows up as thousands of changed lines and tells you nothing about *what* changed.
- **Silent bad merges.** A line-level merge of two STEP files produces a file that is
  syntactically valid and **geometrically wrong**. Nothing warns you. Nobody reviews
  40,000 coordinate lines.

So: mark CAD files as binary (this repo ships a `.gitattributes` that does it), and use a
real geometric comparison instead.

---

## 2. Configure `git difftool`

`caddiff` ships a dedicated `difftool` subcommand for exactly this. It differs from
`caddiff diff` in one way that matters: **it exits 0 when differences are found**.
`git difftool` treats a non-zero exit from the tool as "external diff died" and aborts the
whole run — so `caddiff diff` (which must return 1 on differences, for CI) is the wrong
command here.

```console
# register the tool
$ git config --global difftool.caddiff.cmd 'caddiff difftool "$LOCAL" "$REMOTE"'

# don't ask for confirmation on every file
$ git config --global difftool.prompt false
```

Then:

```console
# compare a single file across the last commit
$ git difftool -t caddiff HEAD~1 -- bracket.stp

# compare a whole branch
$ git difftool -t caddiff main -- '*.stp'

# compare a commit range
$ git difftool -t caddiff v1.2..v1.3 -- assemblies/
```

Each invocation writes a fresh report and prints its location:

```
Report: /tmp/caddiff-difftool-a1b2c3/diff_manifest.json
3 difference(s) found — see report.html in the same directory.
```

Open the printed `report.html` for the highlighted images and the change list.

---

## 3. Make plain `git diff` delegate too (optional)

If you want `git diff` itself to route STEP files through caddiff, uncomment the
`diff=caddiff` lines in `.gitattributes` and define the driver:

```console
$ git config --global diff.caddiff.command 'caddiff difftool "$LOCAL" "$REMOTE"'
```

Caveat: this runs caddiff for **every** CAD file in the diff, and a single run takes tens
of seconds on a real assembly (rendering dominates — see the benchmark table in the
README). On a branch that touches 20 parts that is a coffee break. `git difftool` (on
demand, per file) is usually the better trade.

---

## 4. Choosing which version to compare

`git difftool` passes git's "before" file as `$LOCAL` and "after" as `$REMOTE`. To control
which revisions those are, use normal git revision syntax:

| Goal | Command |
|---|---|
| Working tree vs last commit | `git difftool -t caddiff -- model.stp` |
| Last commit vs the one before | `git difftool -t caddiff HEAD~1 -- model.stp` |
| Two branches | `git difftool -t caddiff main..feature -- model.stp` |
| Two tags | `git difftool -t caddiff v1.0..v2.0 -- model.stp` |
| Two arbitrary revisions | `git difftool -t caddiff <rev1> <rev2> -- model.stp` |

Labels on the generated images come from the file names git uses (temporary names, so
they are not informative). Pass `--label-old` / `--label-new` yourself if you want the
images to say `v1.2` / `v1.3`:

```console
$ git config --global difftool.caddiff.cmd \
    'caddiff difftool "$LOCAL" "$REMOTE" --label-old v1.2 --label-new v1.3'
```

---

## 5. CI: use `caddiff diff`, not `caddiff difftool`

Inside CI you *want* the non-zero exit code — that is the gate:

| Exit code | Meaning |
|---|---|
| `0` | no differences |
| `1` | differences found |
| `2` | the run failed (bad input, timeout, crash) |

```yaml
- run: |
    docker run --rm -v "$PWD:/data" ghcr.io/angel291592/caddiff:edge \
      diff /data/old.stp /data/new.stp -o /data/report
  # exit 1 fails the step, which is what you want on a PR that changes geometry
```

Note that `caddiff difftool` returns `0` even when differences exist, so it must **not** be
used as a CI gate.
