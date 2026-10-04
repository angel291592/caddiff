# Security Policy

## Supported versions

| Version | Supported |
|---|---|
| `latest release` (most recent tagged release) | Yes |
| `main` | Yes |
| Older tagged releases | No - please reproduce on `latest release` or `main` first |

## Reporting a vulnerability

**Report privately. Do not open a public issue.**

Use GitHub Security Advisories: open the **Security** tab of this repository and
choose **Report a vulnerability**, or go directly to:

```
https://github.com/<owner>/<repo>/security/advisories/new
```

A private advisory lets us discuss, fix, and coordinate disclosure with you
before anyone else can read the details.

### What to include

- The version you tested (release tag, or the image tag/digest if you used the
  Docker image).
- The exact command you ran and the exit code you observed.
- A **minimal synthetic sample** that reproduces the problem, or a short script
  that generates it (see the note on CAD data below).
- What you expected to happen, what actually happened, and why you think it is a
  security problem rather than a plain bug.
- Any suggested fix, if you have one.

### What to expect

This is a volunteer-run project, so we cannot promise a fixed response time. We
will acknowledge your report as soon as we can, keep you updated on the fix, and
credit you in the advisory unless you ask us not to. Please give us a reasonable
window before disclosing publicly.

## What this project does not do

- **It never uploads your CAD files anywhere.** There is no server component and
  no upload path. Your STEP/STP files are read locally by the process you
  started, and the reports are written to the output directory you chose.
- **It runs fully offline.** No telemetry, no analytics, no usage reporting, no
  update check, and no outbound network requests at runtime - including no calls
  to any external model or API. The only network access involved in this project
  happens while the Docker image is being built, when package managers fetch
  their packages.
- **It keeps no state between runs** beyond the files you ask it to write.

If you observe any network traffic, file access outside the paths you passed, or
any other behaviour that contradicts the above, treat it as a security report.

## CAD files are confidential - do not attach real ones

CAD models routinely contain trade secrets: unreleased products, customer part
numbers, tooling dimensions, supplier names. Treat every real model as
confidential.

When reporting a problem:

- **Do not attach a real customer or employer model**, and do not attach a
  report (PNG/HTML/Markdown/JSON/PPTX) generated from one - the derived files
  carry the same part names and dimensions as the source.
- **Reproduce with a minimal synthetic sample instead.** Two tiny STEP files that
  differ in exactly one feature are almost always enough, and you can generate
  them yourself in a few lines. If the bug needs a specific shape, describe the
  shape in words and we will build the fixture together.
- If you believe the problem cannot be reproduced without the real file, say so
  in the advisory and describe the file's structure instead of sending it.

## Scope

In scope:

- Anything that makes `caddiff` write outside the output directory, read files it
  was not asked to read, or leak local paths or file contents into a published
  artifact.
- Resource exhaustion: an assembly crafted to make the tool consume unbounded
  memory, disk, or time, or to escape the configured timeout.
- A malicious or malformed STEP/STP input that makes the FreeCAD subprocess
  misbehave in a way this project should defend against (missing limits, missing
  validation, unsanitised input reaching a shell or a path).
- Weaknesses in how the Docker image is built or configured.

Out of scope for this repository (report upstream), unless our usage makes the
issue reachable in a way that matters:

- Memory-safety bugs inside FreeCAD, Open CASCADE Technology, Pillow, numpy, or
  the Python runtime itself. Upstream is the right place for those; still tell us
  if `caddiff` is the thing that exposes them.
- Known limitations that are documented as limitations rather than defects, for
  example the accuracy limits of the part-pairing heuristic or the absence of
  free-form tolerance analysis.

## Dependencies

The Docker image ships third-party components under their own licenses. See
[THIRD_PARTY_LICENSES.md](THIRD_PARTY_LICENSES.md) for what is included and under
which terms. If you are reporting a vulnerability in one of those components,
please also notify its upstream project.
