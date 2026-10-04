<!--
Thanks for the pull request. A few of these fields exist because reviewers kept having
to ask for them; the empty ones are worse than a short honest answer.
Conventional Commits in the PR title are required — release-please derives the CHANGELOG
and the version bump from them (see .github/workflows/release-please.yml).
-->

## What changed, and why

<!--
The motivation, not the diff. What was wrong or missing, what a user could not do
before, and why this is the right fix. Link the issue if there is one.
-->

## Before / after images — required for geometry or rendering changes

<!--
Any change to geometric classification, boolean diffing, viewpoint selection, the
highlight overlay or the report layout must show the images it produces. Attach the
PNGs (drag them in) and say which fixture produced them, e.g.:

  python caddiff/make_moved_fixture.py examples/fixtures
  caddiff diff examples/fixtures/moved_old.stp examples/fixtures/moved_new.stp -o out

before: <image>
after:  <image>

If the change cannot affect the output pixels, write "not applicable — <reason>".
-->

## Tests run

<!--
Paste the ACTUAL output, not a claim. For the offline suite:

  $ pytest tests/unit
  <paste output>

If the change touches geometry or rendering, the FreeCAD path cannot run in
`tests/unit` — say how you exercised it (image + fixture, end-to-end command,
exit code observed).
-->

```console

```

## Does this change `diff_manifest.json`?

<!--
The manifest is a machine contract: renaming or removing a field is a breaking change
that needs a minor version bump and a CHANGELOG entry under `Unreleased`.
Adding a field is fine, but say so — downstream CI reads this file.
-->

- [ ] No field added, renamed or removed
- [ ] Field(s) added (backwards compatible)
- [ ] Field(s) renamed or removed (breaking — version bump + CHANGELOG entry included)

<!-- If anything is ticked above other than the first box, describe it and the
     compatibility impact here. -->

## Checklist

- [ ] The PR title is a Conventional Commit (`feat:`, `fix:`, `docs:`, `ci:`, …).
- [ ] No customer CAD data, part names, model excerpts or derived reports are included
      (PNG/JSON/HTML/PPTX carry plain-text part names too).
- [ ] No secrets, tokens, internal hostnames or absolute local paths are included.
- [ ] New user-visible text goes through `i18n.t()` with both `en` and `zh` strings
      (console progress output stays English and does not need i18n).
- [ ] The exit-code contract is untouched — or, if it is touched, the change is explained
      above (`0` no differences, `1` differences found, `2` execution failure).
- [ ] Documentation that this change contradicts has been corrected in the same PR
      (`README.md`, `docs/`, module docstrings).
