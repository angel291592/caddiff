# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

<!--
This file is maintained by release-please (see .github/workflows/release-please.yml
and release-please-config.json). Every release pull request rewrites it from the top
down to the latest released version: version headings, dates, compare links and the
commit-derived bullets. Consequences:

  * hand-edit ONLY the `## [Unreleased]` section. Use it for changes a commit subject
    cannot carry — above all, a field rename in `diff_manifest.json` and its
    compatibility impact (the manifest is a machine contract, see AGENTS.md §3).
  * never hand-edit a released section: the next release pull request overwrites it.
  * release-please bumps `caddiff/version.py` (the single source of truth for the
    version) and writes new sections with an inline compare link and a date. The
    `[0.1.0]` section below is hand-written and predates the automation, so it
    carries no date yet — add one when `v0.1.0` is actually tagged.
-->

## [Unreleased]

## [0.1.0]

First public release.

### Added

- **BOM alignment** — parts are matched across the two revisions by name, with
  bounding-box proximity plus a 50 % volume guard for repeated instances. Added,
  removed and count-changed parts are reported; unmatched parts are listed, never
  guessed.
- **Geometric three-state classification** — every aligned pair is classified as
  `identical`, `moved` or `shape_changed`, so "the same part in a new place" is
  never reported as a modified part.
- **Boolean symmetric-difference highlighting** — `shape_changed` is decided by a
  real boolean symmetric difference, run in a killable subprocess with a hard
  timeout, and the changed volume is highlighted on the rendered images.
- **26-direction automatic viewpoint selection** — 6 faces + 12 edges + 8 corners
  are scored for how much of the highlighted geometry they show and how readable
  the framing is, and the winner is picked per change. When the change is buried
  inside the assembly, the image says so instead of pretending the shot is fine.
- **HTML, Markdown and JSON reports** — `report.html` (images inline),
  `report.md` (for pull request comments) and `diff_manifest.json`, the
  machine-readable contract that also carries the honesty fields: unpaired parts,
  skipped parts and threshold-filtered differences are always in there, so
  "not mentioned" can never be read as "no difference".
- **Exit codes as a CI gate** — `0` no differences, `1` differences found,
  `2` execution failure (unreadable input, subprocess timeout or crash). A crashed
  geometry kernel can never be mistaken for "nothing changed".
- **Single-machine Docker image** — `deploy/Dockerfile` bundles FreeCAD,
  OpenCASCADE and Xvfb, so one command is the whole interface:
  `docker run --rm -v "$PWD:/data" ghcr.io/angel291592/caddiff:edge diff old.stp new.stp -o report`.
  The CLI itself has zero third-party dependencies.

### Notes

- English output is the default (`--lang en`); `--lang zh` is available. Console
  progress output is always English.
- Deliberately out of scope for this release: free-form surface tolerance
  analysis, GD&T annotation, and very large assemblies.

[Unreleased]: https://github.com/angel291592/caddiff/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/angel291592/caddiff/releases/tag/v0.1.0
