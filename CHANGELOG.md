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

## [0.4.5](https://github.com/angel291592/caddiff/compare/v0.4.4...v0.4.5) (2026-10-31)


### Documentation

* **release:** bump the pinned image/action tags to v0.4.4 ([d6d50ec](https://github.com/angel291592/caddiff/commit/d6d50ec23f5b0c269c0bbe69b4f917683f989de6))

## [0.4.4](https://github.com/angel291592/caddiff/compare/v0.4.3...v0.4.4) (2026-10-30)


### Documentation

* **pipeline:** record the FreeCAD 26.3 end-to-end run the reporter confirmed ([59181c7](https://github.com/angel291592/caddiff/commit/59181c7b43fa7362461f275ddc27c3f48a15ed0b))

## [0.4.3](https://github.com/angel291592/caddiff/compare/v0.4.2...v0.4.3) (2026-10-27)


### Documentation

* **docker:** correct the xvfb-run readiness story, drop the x11-utils claim ([1c25491](https://github.com/angel291592/caddiff/commit/1c254912f745c2494ab933516504c8b76c6e49b6))
* **release:** bump the pinned image/action tags to v0.4.2 ([153075c](https://github.com/angel291592/caddiff/commit/153075c9738d5efa6eebd89267d9286c1a8b425b))

## [0.4.2](https://github.com/angel291592/caddiff/compare/v0.4.1...v0.4.2) (2026-10-21)


### Documentation

* **git-integration:** document the design-time review / CI gate split ([9057620](https://github.com/angel291592/caddiff/commit/905762099ab47da0b6be367a3e1af7c4b9a40d3e))
* point comments at documents readers can actually open ([9b97d5b](https://github.com/angel291592/caddiff/commit/9b97d5b3d16b8d22a7be8e4c40fb03c419f1717c))
* **readme:** complete the Ubuntu 24.04 install steps ([4ff87a4](https://github.com/angel291592/caddiff/commit/4ff87a417bb86e4febf47d97ed0718ff22febb0e))
* **readme:** the roadmap covers the released v0.4 line ([755ae96](https://github.com/angel291592/caddiff/commit/755ae966e76e5a027912ecf4f7197fdb74e87d16))
* **release:** bump the pinned image/action tags to v0.4.1 ([2e5b611](https://github.com/angel291592/caddiff/commit/2e5b61130cd3e3d914f46eff70bd4f82427d2ddb))

## [0.4.1](https://github.com/angel291592/caddiff/compare/v0.4.0...v0.4.1) (2026-10-20)


### Bug Fixes

* **boolean_worker:** restore the structured error output v0.4.0 dropped ([abffb44](https://github.com/angel291592/caddiff/commit/abffb4402f957d4621f920b6005b5ddbe14b5d29))


### Documentation

* **release:** bump the pinned image/action tags to v0.4.0 ([1a94acd](https://github.com/angel291592/caddiff/commit/1a94acd4d71b779489387d11f9b1856d10cc35cc))

## [0.4.0](https://github.com/angel291592/caddiff/compare/v0.3.7...v0.4.0) (2026-10-20)


### Features

* **perf:** add an opt-in volume-based performance summary (`--perf`) ([fc9f39e](https://github.com/angel291592/caddiff/commit/fc9f39e2b8e592cf7633b6ab34ced14e0c536dc8))


### Bug Fixes

* cover FreeCAD 26 in the perf probe and the fixture generator ([267a09d](https://github.com/angel291592/caddiff/commit/267a09dea5a9be0d9f85f5d1201895f4efd60834))
* import FreeCAD first so FreeCAD 26 does not clear module names, and document the Ubuntu install ([#12](https://github.com/angel291592/caddiff/issues/12)) ([f9954a4](https://github.com/angel291592/caddiff/commit/f9954a43d4d409c60d4124b9aa68f4e756563644)), closes [#11](https://github.com/angel291592/caddiff/issues/11)
* **perf:** close the honesty gaps an independent review found in `--perf` ([1be825b](https://github.com/angel291592/caddiff/commit/1be825b81b2b162c707b0199905c224118cab00c))


### Documentation

* record the FreeCAD 26 import-order and render-teardown pitfalls ([881d95f](https://github.com/angel291592/caddiff/commit/881d95f95be0491b081252c070f82796e6025cdf))
* **release:** bump the pinned image/action tags to v0.3.7 ([96e0dff](https://github.com/angel291592/caddiff/commit/96e0dff158b232ae1e2c27cecca74fdcc915778f))

## [0.3.7](https://github.com/angel291592/caddiff/compare/v0.3.6...v0.3.7) (2026-10-16)


### Bug Fixes

* **report:** percent-encode image filenames in report links ([929f616](https://github.com/angel291592/caddiff/commit/929f616e43f3fa5cf6c036d7fbbd1c8b5316ae95))


### Documentation

* **release:** bump the pinned image/action tags to v0.3.6 ([67dcfcf](https://github.com/angel291592/caddiff/commit/67dcfcf85c741aa58381281e0367c674a699a5ce))

## [0.3.6](https://github.com/angel291592/caddiff/compare/v0.3.5...v0.3.6) (2026-10-11)


### Documentation

* **release:** bump the pinned image/action tags to v0.3.5 ([868ec89](https://github.com/angel291592/caddiff/commit/868ec8973d4b56096db0b146230809444f728ba7))

## [0.3.5](https://github.com/angel291592/caddiff/compare/v0.3.4...v0.3.5) (2026-10-10)


### Bug Fixes

* **exit-codes:** map a step that cannot start to exit 2, not 1 ([454b65f](https://github.com/angel291592/caddiff/commit/454b65f22a120b92363e279e77b04842a7673294))
* **render:** fail on an empty close-up instead of publishing a blank panel ([a243ff2](https://github.com/angel291592/caddiff/commit/a243ff2cb1c1ed42d3cd22c7d83b7f80db3cfaf3))
* **report:** fall back to the shared labels instead of a literal "?" ([374fb14](https://github.com/angel291592/caddiff/commit/374fb14510a0f78649dcf58dca676e192507b4b5))


### Documentation

* **readme:** drop the stale competitor star count ([fbdb986](https://github.com/angel291592/caddiff/commit/fbdb9865a01034f62169fa3e6f96ebf25683dd73))
* **release:** bump the pinned image/action tags to v0.3.4 ([8ce05aa](https://github.com/angel291592/caddiff/commit/8ce05aad3c1ef2fe7c48beda533138f3d606b86c))

## [0.3.4](https://github.com/angel291592/caddiff/compare/v0.3.3...v0.3.4) (2026-10-10)


### Bug Fixes

* **fcenv:** probe interpreter candidates by execution, not by existence ([5d3c5d2](https://github.com/angel291592/caddiff/commit/5d3c5d20af6793b61a1dee15e2a91e89e1bfffe3))


### Documentation

* **readme:** stop pointing FREECAD_PYTHON at the FreeCAD GUI app ([e78a2ea](https://github.com/angel291592/caddiff/commit/e78a2ea781baeba95ffd61c9f0afa6acdb790e1c))
* **release:** bump the pinned image/action tags to v0.3.3 ([bb22d0e](https://github.com/angel291592/caddiff/commit/bb22d0e9e99bef20b35c822e9565281cc16af2f7))

## [0.3.3](https://github.com/angel291592/caddiff/compare/v0.3.2...v0.3.3) (2026-10-08)


### Documentation

* **readme:** stop the roadmap claiming v0.1 is 'this release' ([57c5a8b](https://github.com/angel291592/caddiff/commit/57c5a8b91e2f51bda7b0743f99ab021b1ff75879))
* **release:** make README links resolve on PyPI, fill in the PyPI sidebar ([6a315ea](https://github.com/angel291592/caddiff/commit/6a315ead7526d6e2f0d079f7fb49e5a0623021ba))
* **release:** pin the released image tag everywhere, add the social preview ([026a90c](https://github.com/angel291592/caddiff/commit/026a90c3a351b4380f45889d6975eb7baab0d878))

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
