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
    compatibility impact (the manifest is a machine contract — renaming or removing a
    field is a breaking change; see CONTRIBUTING.md).
  * never hand-edit a released section: the next release pull request overwrites it.
  * release-please bumps `caddiff/version.py` (the single source of truth for the
    version) and writes new sections with an inline compare link and a date. The
    `[0.1.0]` section below is hand-written and predates the automation, so it
    carries no date yet — add one when `v0.1.0` is actually tagged.
-->

## [0.3.2](https://github.com/angel291592/caddiff/compare/v0.3.1...v0.3.2) (2026-10-05)


### Documentation

* **pipeline:** rewrite module docs in English, mirror stays at pipeline.zh-CN.md ([c233f6a](https://github.com/angel291592/caddiff/commit/c233f6ab0d0ab9cba7e5247bfdf6233e270fe739))
* **readme:** add GitHub Action usage, pin released image tags, link the demo report ([f52aad2](https://github.com/angel291592/caddiff/commit/f52aad2c5a43a054e4f8ffef846019a7ac24ea0b))
* **zh:** add Simplified Chinese mirrors for all public docs ([3575ae2](https://github.com/angel291592/caddiff/commit/3575ae26e7d01a3eb449a37a6b4685988d009f21))

## [0.3.1](https://github.com/angel291592/caddiff/compare/v0.3.0...v0.3.1) (2026-10-05)


### Bug Fixes

* **geom:** report same-name parts the pairing guard rejects, never drop them ([62e89ca](https://github.com/angel291592/caddiff/commit/62e89cae0f0d7a7f291ed08416736280050e40fb))
* **pptx:** stop silently dropping unresolved notes on the no-difference slide ([ebd491c](https://github.com/angel291592/caddiff/commit/ebd491cc48e7e2ac26c813a89fcac1ba66fd5982))

## [0.3.0](https://github.com/angel291592/caddiff/compare/v0.2.1...v0.3.0) (2026-10-04)


### Features

* **cli:** let the render deadline be raised instead of silently truncating large runs ([efb2d3e](https://github.com/angel291592/caddiff/commit/efb2d3e08827c816bcc25cee7339cfbd7ee0029f))


### Bug Fixes

* **bom:** stop reading SolidWorks STEP files as empty assemblies ([b222576](https://github.com/angel291592/caddiff/commit/b222576044188f5f3d2118d465933754c408bfb4))
* **changelog:** drop commit links the history rewrite invalidated ([9d7c110](https://github.com/angel291592/caddiff/commit/9d7c110ce8ca0fa69dfa8c92c15784d0fe2f12f8))
* **gate:** name-based blocking for the local agent spec, on all three triggers ([a6acb15](https://github.com/angel291592/caddiff/commit/a6acb15559712f595c8d1d6562b3a68d3c3fb4e6))
* **pipeline:** refuse to report "no differences" when nothing was parsed ([d814b51](https://github.com/angel291592/caddiff/commit/d814b515bcd8afcd26eefc0dffbf808088317ea4))
* **render:** clip diff rects instead of inverting them, and never drop silently ([ea4d4a1](https://github.com/angel291592/caddiff/commit/ea4d4a1e39faf016c7249f686d830d550b475d7f))


### Performance Improvements

* **render:** stop re-fitting the camera for every candidate view ([263ebf5](https://github.com/angel291592/caddiff/commit/263ebf5d503463879832ee61a2cf8f149b7c696c))


### Documentation

* **ci:** record that the image is live, and the tag trigger that never fires ([16724b9](https://github.com/angel291592/caddiff/commit/16724b94923458565fc910589c2df3139701b99f))
* stop pointing readers at a file the repository does not ship ([add7d5f](https://github.com/angel291592/caddiff/commit/add7d5f91426db33618b7b8e16ae1da58437ab54))

## [0.2.1](https://github.com/angel291592/caddiff/compare/v0.2.0...v0.2.1) (2026-10-04)


### Bug Fixes

* **report:** keep the code parseable on the Python versions we claim

## [0.2.0](https://github.com/angel291592/caddiff/compare/v0.1.0...v0.2.0) (2026-10-04)


### Features

* initial public release — caddiff v0.1.0
* **security:** add a fail-closed sensitive-data gate


### Bug Fixes

* **ci:** spell the command correctly, and drop the placeholders CI never exercised
* **diff:** degrade to a volume-delta verdict when the boolean degenerates
* **diff:** stop collapsing distinct parts into one candidate
* **docker:** install x11-utils so the container can actually run
* **docker:** make the image actually run -- the interpreter was a GUI app
* **gate:** stop flagging this repository's own git object names
* **packaging:** make the console script work after pip install
* **render:** truncate the banner subtitle too, and count BOM-only diffs correctly


### Documentation

* fix a stale reference to a CLAUDE.md that does not exist
* record what running the image for real actually found

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
