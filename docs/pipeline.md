# Capability 1: STP Assembly Diff Pipeline (`caddiff/`) — Module Functional Documentation

English | [简体中文](pipeline.zh-CN.md)

> This is the **single detailed document** of the diff capability line: reading it is enough to modify/debug every script under `caddiff/`.
> For outward-facing usage see [`../README.md`](../README.md); for the artifact layout and exit-code semantics see §5 of this document.
> Pitfall numbering (A~I, G1~G7) keeps its historical letters and is not renumbered when referenced across documents.

---

## 0. Capabilities and Status

Two STPs in (old and new revisions of the same assembly): the diff is computed at the geometry layer → rendered as magenta highlight + red rectangle callouts → generating a diff-list report (HTML / Markdown) and a machine-readable manifest (JSON); with `--pptx` a comparison PPT is additionally emitted.

- **Status**: PoC complete and verified end to end (173/173 assertions); multi-diff-point rectangle callout change 55/55; generalization refactor **P0 (robustness) completed (18/18 assertions, 2026-08-27)**; **P1 (pairing correctness) and P2 (scalability) not implemented**.
- Outputs: `<out>/report.html` (human-readable report) + `report.md` (for PR comments) + `diff_manifest.json` (machine contract), with diff images under `<out>/images/*.png`; with `--pptx` additionally `compare.pptx`, 3 slides, each slide: three images in a horizontal row (old-revision closeup / new-revision closeup / overview) + a structured info band below (left column numeric: diff size / removed material / added material; right column qualitative: overview viewpoint; legend at the bottom). Every image carries a direction banner on top (title + view-direction vector + three-colored coordinate-axis arrows, font size scaled proportionally to image width); the overview viewpoint is auto-selected per diff. Multiple spatially separated diffs on the same part each get their own rectangle; circled digits (①②…) are placed in blank space outside the rectangle and tied back to the rectangle edge by leader lines; the left column lists sizes per cluster. All code lives under `caddiff/` and never modifies the original STP/PPT.
- **Generalization capabilities delivered with P0**: a no-difference run still produces the explanation page without crashing; assembly position/orientation changes are detected as the `moved` type and presented separately (previously missed silently); booleans moved into a subprocess with a hard timeout; version labels derived from the filename or the CLI; parts skipped / unmatched / threshold-filtered all land explicitly in the artifacts and the PPT.

---

## 1. Core Technical Judgments (why)

- **STEP text cannot be diffed directly**: B-rep entities are linearly ordered by internal numbering; re-exporting the same geometry reshuffles all the numbers, so a text diff is all false differences. Diffs must be computed at the geometry layer (assembly-tree structure + boolean operations).
- **The BOM diff uses string rules, not geometry**: zero cost, 100% deterministic. Part names get an instance-number segment appended at the end by Creo (e.g. `AA-GLUE_1_1_2_3_1_1` → `..._1`); a regexp cuts off the trailing non-letter portion to obtain the `base_name`, used as the **family name**. **Whether folding actually happens is decided jointly by both revisions** (`bom_diff.align_keys`): when the family's name set is identical in both revisions, the **exact name** is used; the family is folded only otherwise — by name alone you cannot distinguish "instance numbers of the same part" from "different parts sharing a prefix", and a wrong fold lets the container rule swallow a whole group of real parts (pitfall J1).
- **The geometric diff runs the symmetric difference only on the candidates the BOM layer screened as "names matching"** (`(A-B)∪(B-A)`), avoiding indiscriminate boolean operations over all parts.
- **"Volume and bbox unchanged" does not mean "no diff"**: both quantities are completely invariant under translation and partly invariant under 90°/180° rotation. Measured: `box` vs `box translated 50mm` would be classified as no diff and skipped, while the true symmetric difference reaches 400mm³ (2× the part volume; the two shapes have zero overlap) — **assembly position-adjustment changes were previously missed entirely and unreported**. Hence the three-way classification: `identical` (the only tier allowed to be silently skipped) / `moved` (records translation and rotation; no boolean) / `shape_changed` (runs the symmetric difference). The `moved` criteria are deliberately strict (volume **and** bbox edge lengths must **all** match after sorting); a misjudgment falls onto the safe side `shape_changed`.
- **Red-rectangle coordinates must come from exact 3D bbox projection, not from "looking at the picture and guessing the diff's position"**: where a rectangle goes is a recomputable geometric quantity — that is what makes it assertable.
- **Color must highlight the diff geometry itself; red rectangles are only an aid**: the three diffs together are 0.35%~3.97% of the part's own bulk; surface/edge deformations of that magnitude are indistinguishable by eye under a grey render even with "a rectangle around the area". Only overlaying the symmetric-difference geometry in opaque magenta on the semi-transparent part makes them visible at a glance.
- **Red rectangles must be drawn per "diff cluster", never as a union bbox**: a symmetric difference is usually composed of several spatially separated pieces of material; taking the overall BoundBox degenerates into "one giant rectangle around the whole part", where the rectangle loses its locating meaning. Measured: PART-A's added material is two pieces with centers 18.3mm apart while the part is only about 19mm wide, so the union bbox equals the whole part exactly — the piece that stretched it is only 16% of the diff volume. The approach: split `removed`/`added` into clusters by `Shape.Solids`, project and frame per cluster, then merge rectangles that overlap after projection (see §6, the H-group pitfalls for details).
- **The symmetric difference is split into removed/added halves**: `removed` (present in old, absent in new = material taken away) is highlighted on the old-revision image; `added` (present in new, absent in old = material newly added) is highlighted on the new-revision image; both images render with the same camera to realize a side-by-side comparison. The semantic difference is explained by the text bands of the report / PPT, not distinguished by color, sparing readers a color legend to memorize.
- **The overview image's viewpoint must be selected automatically per diff; it cannot be fixed at isometric**: fixed `viewIsometric()` points the same way as the closeup images, the diff highlight is mostly blocked dead by the part itself or by sibling parts, and that image's information value drops to zero. The approach: set non-target parts to `CONTEXT_TRANSPARENCY=85`, then scan 26 candidate directions and pick the best by a combined score (see the F-group pitfalls for details).
- **Direction selection cannot rank by magenta pixel count alone**: that picks the orthographic side view with "the most magenta but a smeared wireframe blob", where the assembly is unrecognizable. Readability factors must be layered on top, and **when readability is not met, prefer dropping magenta over keeping the picture** (the overview's job is location; the diff detail is the closeup's job).

---

## 2. Technical Architecture

```
OLD.stp, NEW.stp
   │
   ├─ [1] bom_diff.py (system Python; pure-text parsing of the STEP PRODUCT entities)
   │     → bom_diff.json (adds / removes / candidate classification)
   │
   ├─ [2] geom_diff.py (FreeCAD Python, console mode)
   │     Import.insert loads both revisions → pairing → classify_change three-way classification
   │       ├ identical      → skip (the only tier allowed to be silently skipped)
   │       ├ moved          → no boolean; record translation / rotation angle
   │       └ shape_changed  → boolean symmetric difference (run in the subprocess; hard-timeout killable)
   │     → geom_diff.json (bbox in world coordinates / parent_chain / numeric volume fields /
   │                       change_type / skipped_parts / global_alignment / settings)
   │
   ├─ [3] render_diff.py (FreeCAD Python, GUI mode)
   │     precompute → inject → render  ← the order is a hard constraint
   │     shape_changed emits 3 images (overview / new-revision closeup / old-revision closeup)
   │     moved emits 3 images (overview / new position / old position + translation arrow)
   │     → PNG (written into `<out>/images/`) + render_manifest.json (with magenta-pixel self-check counts; each diff is atomic-flushed to disk as soon as its render finishes)
   │
   └─ [4] report assembly (system Python)
         writes diff_manifest.json first (machine contract, includes summary)
         dispatches the two layouts by change_type; an empty manifest emits the "no diffs detected" explanation page
         → report.html / report.md; with --pptx additionally → compare.pptx
```

**Booleans must run in the subprocess (never rolled back into a direct call)**: FreeCAD's boolean is a blocking C++ call; `signal.alarm` is unavailable on Windows and threads cannot interrupt it either. Measured: `PART-B` has only **912 faces** yet a single `cut` ran **840s without returning** — face count does not correlate with boolean cost, and no face-count gate can stop it. `boolean_worker.py` passes only that single part's geometry via `exportBrep`/`importBrep` (overhead is only 0.6s end to end for a normal part, no need to re-import the STP inside the subprocess); when the parent times out it `kill`s the child, and the part is recorded into `skipped_parts`. **The subprocess must `import FreeCAD` before `import Part`** — importing Part directly crashes with a 0xC0000005 access violation (measured `rc=3221225477`).

**Environment constraints (must be observed)**: `geom_diff.py` and `render_diff.py` must be run with an interpreter that **can `import FreeCAD`** — `caddiff/fcenv.py` is the **single source of truth** for interpreter path resolution (`FREECAD_PYTHON` → `FREECAD_HOME` → common Linux install paths → `PATH`; raises with repair guidance when nothing is found, **no silent fallback to the current interpreter**). `bom_diff.py` and `build_pptx.py` are pure logic and use the system Python.

The criterion is "**that interpreter can run `script.py args`**", not "what it is called" — the two can diverge, and measurement has hit the divergence:

- **Windows**: the FreeCAD distribution's `bin/python.exe` is a real interpreter; use it directly. Its Part/Gui are compiled extensions bound to py3.11 and cannot be installed into the system 3.14.
- **Linux (apt / official PPA)**: `/usr/lib/freecad/bin/freecad-python3` is **not an interpreter** — it is a 113KB **GUI application** with an embedded Python. Hand it a script and the positional args get read as "documents to open", so it launches the whole GUI and then **never returns** (measured: pipeline step 2 timed out at 300s with `rc=124`, container CPU 0%, zero output — it looks hung). The correct approach is the system `python3` + `PYTHONPATH=/usr/lib/freecad/lib` — that is exactly how the image is configured. The earlier statement "only the bound Python works" holds only for the Windows distribution: Linux packages install the modules into that directory and the distro's python3.12 can use them directly.

`deploy/Dockerfile` nails this constraint down as build-time assertions, and the assertions cover more than "the `-c` form can import": also that a **script file** can be executed and that an `ActiveView` can be obtained under a virtual display — the first extra assertion exists precisely to block the pitfall above (`freecad-python3` passes `-c` but fails "execute a script file").

---

## 3. How to Run

```bash
# One command runs the whole pipeline (system Python entry; it internally switches to the FreeCAD Python for steps 2/3)
caddiff diff examples/demo_old.stp examples/demo_new.stp -o caddiff-out
# Measured at about 355s (BOM 0s / geometry 155s / rendering 197s / PPT 1s)
# The geometry step got slower than the earlier 89s because PART-B is no longer hard-code skipped:
# it now really runs one boolean and hits the 60s timeout (+62s), plus BREP write-to-disk overhead.
# That is the price of "does not hang on any arbitrary STP", not a performance regression.

# Options (all have sensible defaults)
#   -o, --out DIR                   output directory (default ./caddiff-out)
#   --label-old A --label-new B     revision labels on the images and in the report (default: derived from the filename; must be given when filenames are hashes)
#   --lang {en,zh}                  artifact language, default en (English first)
#   --pptx                          additionally export the comparison PPT (not exported by default)
#   --max-faces 5000                above this face count, skip the boolean outright (cheap pre-gate)
#   --boolean-timeout 60            hard timeout seconds for a single boolean
#   --skip-parts NAME1,NAME2        explicitly skip the named parts (exact match on base_name)
#   --min-diff-pct 0.1              relative threshold: the diff must exceed this percentage of part volume (default 0 = disabled)
#   --version / --help
```

All artifacts land under `<out>`: `diff_manifest.json` (machine contract, includes `summary`), `report.html`, `report.md`, `images/*.png` (diff images), `images/render_manifest.json` (**the intermediate contract between the render module and the report module**: per-diff image filenames and pixel self-check counts; field contract in §8), plus the intermediate artifacts `bom_diff.json` and `geom_diff.json`; with `--pptx` there is additionally `compare.pptx`.

Exit codes are an **outward contract** and can be used directly as a CI gate: `0` = the two revisions differ nowhere · `1` = diffs detected · `2` = execution failure (unreadable input / subprocess timeout or crash / internal error).

Per-script arguments for step-by-step debugging (note that step 3 takes **4 arguments** — it needs both the old and the new STP):

```bash
python caddiff/bom_diff.py <stp_old> <stp_new> <bom_diff.json>
<FreeCAD>/bin/python.exe caddiff/geom_diff.py <bom_diff.json> <stp_old> <stp_new> <geom_diff.json>
<FreeCAD>/bin/python.exe caddiff/render_diff.py <geom_diff.json> <stp_old> <stp_new> <images_dir>
python caddiff/build_pptx.py <render_manifest.json> <compare.pptx>
```

**Synthetic fixture (the only end-to-end path that exercises the `moved` branch)**: in the real STP all three diffs are `shape_changed`, so the `moved` branch cannot be reached on this assembly. Generate a pair of synthetic STPs with `make_moved_fixture.py` (SLIDER translated 5mm / BRACKET with 192mm³ removed / BASEPLATE unchanged) and verify:

```bash
<FreeCAD>/bin/python.exe caddiff/make_moved_fixture.py _fixture
caddiff diff _fixture/moved_old.stp _fixture/moved_new.stp -o _fixture/out2 \
       --label-old REV-A --label-new REV-B
```

---

## 4. Measured Baselines (when doing regression, cross-check the assertions against these numbers)

**Windows (re-measured 2026-08-27)**: all three diffs are **pure added material or pure removed material, never mixed** — this fact directly decides how the verification assertions must be written:

| Part | new object TypeId | removed mm³ | added mm³ | diff share | clusters | closeup rectangles | overview viewpoint | saturated magenta inside overview rectangles | new-revision closeup | old-revision closeup |
|---|---|---|---|---|---|---|---|---|---|---|
| PART-C | Part::Feature | 1.6705 | 0 | 0.35% | 2 (1.1591 / 0.5113) | 1 (merged because the two clusters' projections overlap) | `-1-1-1` (fallback) | 0 (hidden inside; located by the red rectangle) | 0 (nothing added) | 68628 |
| PART-D | Part::Feature | 0 | 8.3391 | 3.97% | 1 | 1 | `-1+1+1` | 172 | 56803 | 0 (nothing removed) |
| PART-A | **App::Part** | 0 | 4.8658 | 1.41% | 2 (4.1018 / 0.7638) | **2** (separate rectangles each) | `-1+1+1` | 199 | 5388 | 0 (nothing removed) |

> `magenta_px_overview` is the count under `count_magenta` (g<110); it wobbles slightly with the semi-transparent blend from non-target parts (measured: PART-A gave 1733 / 1696 across two runs), so it is **not suitable for strict-equality assertions**; to assert, use `overview_sat_in` (fully saturated; measured stable at 0 / 172 / 199) or "whichever side has volume has magenta".

Measured sizes of each diff cluster (`cluster_details.size_mm`, cross-checked during regression):

| Part | Cluster ① | Cluster ② |
|---|---|---|
| PART-C | 0.45×3.40×2.55 (removed) | 0.84×5.89×0.20 (removed) |
| PART-D | 4.06×4.06×3.00 (added) | — |
| PART-A | 1.29×10.97×1.23 (added) | 0.51×3.01×0.56 (added) |

For all images, the amount of "**fully saturated** magenta spilling outside the red rectangles" is **0px**.

**Linux software rendering baseline (measured 2026-08-28, Linux server, xvfb + Mesa)**:

| Part | removed/added | overview_sat_in | magenta_px_overview | view_tag | fallback | closeup_new_px | closeup_old_px |
|---|---|---|---|---|---|---|---|
| PART-C | removed 1.6705 | 0 | 4 | -1+1+1 | true | 0 | 20530 |
| PART-D | added 8.3391 | 212 | 1863 | +0-1+1 | false | 2085 | 0 |
| PART-A | added 4.8658 | 78 | 710 | -1+0+1 | false | 2159 | 0 |

- The qualitative rules "whichever side has volume has magenta" and "sat_in as the location criterion, fallback tiering" still hold on Linux (the removed-material part has sat_in=0 and fallback=true; both added-material parts have sat_in>0).
- Absolute pixel values differ strongly from Windows (Mesa software rendering vs GPU, and the output image is smaller on the xvfb 1280 screen: `rect_line_width_px=8` vs 12 on Windows, `banner_h=203`); **Windows numbers must not be reused for strict-equality assertions**.
- The different viewpoint selections (PART-D `+0-1+1`, PART-A `-1+0+1` vs `-1+1+1` for both on Windows) are expected and confirm the known limitation "magenta_px wobbles with the semi-transparent blend from non-target parts".

**Server end-to-end run (measured 2026-08-28, real STP pair)**:

| Item | Result |
|---|---|
| Exit code | 1 (diffs detected) |
| `diff_count` | 3 (shape_changed=3) |
| Parts with diffs | PART-C / PART-D / PART-A |
| Volume numbers | **exactly identical** to the Windows baseline (removed 1.6705 / added 8.3391 / added 4.8658) |
| Cluster counts | 2 / 1 / 2 |
| skipped_parts | PART-B timeout (912 faces) + top-level assembly too_complex (8553 faces) |
| unresolved | PART-E no geometry match |
| global_alignment | misaligned=false, 61 pairs matched, median shift 0mm |
| Duration | BOM 0s / geometry 157s / rendering 135s / PPT 1s, **about 290s total** |
| PPT (`--pptx`) | 3 slides, 2.0MB |

**Verification assertions must be written this way, otherwise they produce false failures**:
- The criterion is "**whichever side has non-zero volume must have magenta**"; you cannot demand magenta on both sides indiscriminately. For a pure-removed part, 0 magenta on the new-revision image is the correct result (the material exists only in the old revision).
- **Location-type criteria must accept only fully saturated magenta (g<60), not the `count_magenta` g<110 threshold**: with non-target parts at 85 transparency, the low-saturation blend showing through (e.g. `(182,112,205)`) spills outside the red rectangles — that is an artifact, not an error. Measured: counted with g<60, the outside-rectangle count stays 0 on all three overview images.
- Do **not** assert "the overview has magenta" indiscriminately: when a diff is hidden inside the assembly (PART-C is exactly this), none of the 26 "legible-looking" directions can catch it; the code then takes the fallback branch with `overview_view_fallback=True` and the overview locates purely via red rectangles. Write the assertions in two tiers.
- For overview picture-quality criteria use `overview_compose`/`overview_ecc`/`overview_body_pct`, **not `overview_view_readability`**: the latter includes the solidity factor (share of the largest magenta connected component); in the fallback tier there is no magenta, solidity is constantly 0, and readability is forced down to the floor 0.15 — using it to judge "the picture is legible" can never succeed.

---

## 5. Pitfalls Hit (all locked down by measurement; mandatory reading before touching the code)

The common trait: **the code raises no error yet the output is wrong**, and **the symptoms are highly misleading** — several pitfalls present as "the rectangle/highlight is in the wrong place", while their root causes are unrelated to each other. Therefore every conclusion must rest on **measured pixels**; neither "reading the code it seems right" nor "it ran through with no error" is accepted.

### A. FreeCAD object-model layer (the most counterintuitive; A1 was the costliest pitfall in this project)

**A1. The `.Shape` property of an `App::Part` container permanently disappears after any Visibility write.**
Bisection measurement: `[baseline] OK V=346.0779` → `[setting Visibility=False on a child of that container] FAIL AttributeError: 'App.Part' object has no attribute 'Shape'` → `[setting Visibility back to True] still FAIL`. `hasattr(obj,'Shape')` flips from True to False — it is not that reading raises; the property itself is stripped off, and **irreversibly**. Only `App::Part` is affected; `Part::Feature` is immune.

The consequences are extremely hidden: in round 1 the pipeline writes `Visibility=True` on all real geometry objects when rendering the overview — which includes the `App::Part` container that is PART-A → the property is stripped on the spot; when the Shape is finally read in round 3 only None comes back → the highlight body silently disappears, giving 0 magenta pixels in the image with no error.

**Fix**: `run()` is strictly split into three phases precompute → inject → render, and **all geometry reads must, as a whole, come before all visibility writes**. This is a hard constraint, not a code-organization preference.

**Do not retry this dead end**: a fallback of "fusing the Group children's Shapes when the container's Shape cannot be fetched" was tried; the measured symmetric difference came out 687.25mm³ (≈341+346; the two shapes have zero overlap) — the children's raw Shapes live in the container's local coordinate system, and the bbox reconstructed via `container placement × child placement` does not match the container Shape's bbox in measurement.

**A2. For a child to render, every ancestor `App::Part` container must be visible**, otherwise the render comes out all white. `show_with_ancestors()` walks up `obj.InList` setting visibility. But **do not also make the top-level assembly container visible** — once visible, an `App::Part` overlays its own aggregate Shape as an independent body, effectively drawing the whole assembly a second time and polluting the picture.

**A3. The ViewProvider of an `App::Part` container has no `Transparency` property** (only the inner `Part::Feature` children do); setting transparency must recurse down to the real geometry objects.

**A4. Every `App::Part` carries a set of Origin datum geometry** (X/Y/Z axes + XY/XZ/YZ planes; 112 such objects across the 16 assemblies in this STP), `Visibility=True` by default, extending far. Blindly making everything in `doc.Objects` visible lets `fitAll()` count them into framing, squeezing the model into a tiny dot at the frame center. **Fix**: filter with `NON_GEOMETRY_TYPES` (`App::Origin`/`App::Line`/`App::Plane`/`App::Point`).

### B. Coordinate system and screenshot layer (three pitfalls stacked; every symptom reads "the rectangle does not match its content")

**B1. `getPointOnScreen()` is bottom-origin (y up), the Pillow image is top-origin (y down); you must flip `y' = viewport height - y`.**
**Why it stays latent so long**: as long as framing is `fitAll` (model centered), the projected y interval is nearly symmetric about the viewport midline — measured for the three diffs: `36..414`, `25..425`, `74..375`, with upper and lower bounds summing to ≈ the viewport height 450 — so values before and after the flip are almost identical and the rectangles look right. It is exposed only when `boxZoom` focuses on a local area **off the frame center**, and the offset grows with the zoom depth (without zooming the top edge overshoots 7px; with a 30%-margin zoom it is 46px).
**Measured fingerprint**: without the flip, **only the top edge overshoots** (left/right/bottom are all 0). "Overshoot on one side only, with the amount varying under zoom" is the signature of a coordinate-origin error; a uniform translation misalignment instead yields an equal margin on the opposite side at the same time.
**Note**: `boxZoom()` takes viewport coordinates (the same bottom-origin system as `getPointOnScreen`); when calling it, **deliberately do not flip y**. The flip exists only to align coordinates onto image pixels; the two places have different purposes — do not unify them.

**B2. The viewport size `av.getSize()` changes during the run** (measured `(600,450)→(603,450)` within one run; `603→609` seen in batch runs). Meanwhile `saveImage(path,w,h)` draws strictly at the size passed in and linearly scales the picture, and `getPointOnScreen` is always relative to the viewport "at the moment of the call". With three sources out of sync, the red rectangles go horizontally out of place by a few pixels.
**Fix**: `capture_and_project()` makes "get size → screenshot → project" one **atomic operation**: the size is taken once and shared by both places, no call that triggers a layout recompute (`fitAll`/`boxZoom`/`updateGui`) is inserted in between, and the size is read once before and once after, alarming on mismatch.

**B3. Projected coordinates are native viewport coordinates, while the output image is enlarged `UPSCALE_FACTOR`×**; before drawing rectangles you must scale the coordinates by the same factor, otherwise the rectangles shrink into the image's top-left corner. (Early on, `saveImage` was also mistakenly passed a hard-coded 1920×1080, which offset all red rectangles and shrank them into one corner of the frame.)

### C. Render-state pollution layer

**C1. The overview re-lights the highlight bodies injected in earlier rounds.** The "show all real geometry objects" loop walks `doc.Objects` indiscriminately, and the highlight bodies `addObject`-injected in earlier rounds are among them. Measured evidence: the overview images of `PART-D` and `PART-A` had **identical** magenta pixel counts and bboxes (89 pixels, same coordinates); the latter was displaying the residue of the former, pointing at the wrong part.
**Fix**: all injected objects are registered in `injected_names` and skipped when rendering overviews; only the current diff's own `DiffHL_*` is explicitly lit; at the end of each round the injected objects are set invisible again and the target part's transparency is restored to 0.

**C2. Pixel-perfect alignment of the old/new closeups relies on "absolutely not touching the camera".** The old-revision closeup must reuse the camera state as it was left at the end of the new-revision closeup, calling no `fitAll`/`boxZoom`/`viewIsometric`, and draws rectangles from the same projected coordinates and screenshots at the same size. **If anyone inserts any camera operation between these two steps in the future, the two images silently misalign**, without an error.

### D. Geometric pairing and framing layer ("the rectangle is right, but what it encloses should not have been compared in the first place")

**D1. The pairing algorithm once compared two unrelated parts.** `pair_by_proximity()` originally did greedy nearest-neighbor purely by bbox-center distance, and its candidate set only accepted `Part::Feature`. Measured: in the new STP, `PART-A-703` had been rebuilt by Creo into an `App::Part` subassembly container (`build_label_map` does collect `App::Part`, but `pair_by_proximity` internally filtered once more by `TypeId=="Part::Feature"`, voiding the collection), so the old-revision shell of 341mm³ was mismatched onto an unrelated 75mm³ thin plate; the resulting "416mm³ diff" was a pure numeric artifact, while the real 703 part was silently missed without an error.
**Fix**: `get_shape()` fetches Shape uniformly (an `App::Part` exposes its Group's aggregate Shape directly; measured 346.08mm³ against the old revision's 341.19mm³ — a strong match confirming the same physical part was rebuilt across revisions); pairing was switched to a **combined distance + volume-similarity score** (candidates with a volume difference >50% are excluded outright); old objects that fail to pair are explicitly recorded in `unpaired_old_labels` instead of being dropped silently. After the fix, that part's real diff came down to 4.87mm³.

**D2. Closeups must not stop at `fitAll` on the whole part**, otherwise a few-mm local change drowns inside a part tens of mm in size. **Fix**: first `fitAll` to locate, then use `av.boxZoom()` to zoom into the currently projected diff bbox (keeping a 30% margin). The `boxZoom` signature is `boxZoom(x1,y1,x2,y2)` — **four int arguments; it does not accept a tuple** (measured and confirmed).

### F. Automatic overview viewpoint selection

**F1. The root cause of occlusion is "the non-target parts are opaque", not "there is no good viewpoint".**
Previously the overview set only the target part semi-transparent while all other parts stayed opaque. Measured: 26 directions × 4 context transparency tiers:

| Part | ctx=0 | ctx=50 | ctx=70 | ctx=85 |
|---|---|---|---|---|
| PART-C | 0/26 directions visible | 0/26 | 3/26 | 7/26 |
| PART-D | 19/26 | 19/26 | 19/26 | 19/26 |
| PART-A | 26/26 | 26/26 | 26/26 | 26/26 |

Take `CONTEXT_TRANSPARENCY=85`. This item turned "0 magenta in the overview" from an accepted limitation into a solvable problem.

**F2. Selecting a direction by magenta pixel count alone picks a smeared wireframe-blob image.** Measured: the pixel-count champion for PART-C was the orthographic side view `-1+0+0` (156px), but that image's solid-area share was only 0.028 and the magenta shattered into 16 connected components — the assembly is completely unrecognizable. Hence readability factors were introduced, with **two-level selection**: first pick the highest combined score among candidates with "truly visible magenta (≥30px) and adequate readability (≥0.25)"; if that tier is empty, fall back to "pick the direction with the clearest picture" and set `fallback=True`, relying on red rectangles for location.

**F3. The visibility threshold must not be written as "non-zero".** In direction `-1+1+0` the same diff has exactly **1** fully saturated magenta pixel — non-zero, but just one dot in a 603×450 viewport, invisible to the eye. With a threshold of only `>0`, this direction would oust the correct fallback decision and produce an image that "looks like nothing yet explains nothing". Hence `SCORE_MIN_VISIBLE_PX=30`.

**F4. Distinguishing "the model fills the frame" from "a slanted streak" needs eccentricity, not bbox fill ratio.**
Another detour taken along the way: the first attempt used "the content's bounding-box fill ratio", measured to be **inverted and undiscriminating** — slanted-streak viewpoints scored 0.398, the isometric baseline 0.656 — ranking by it actually selects the slanted ones. Switching to the eccentricity of the content's pixel distribution (the ratio of the covariance's principal/secondary axis standard deviations) truly separates them: the four slanted-streak directions score ecc=2.41~2.50, the rest 1.04~2.02, and the isometric baseline is 1.51. Hence `SCORE_ECC_LIMIT=2.2`, and **passing the limit scores 0 outright rather than discounted** (this kind of composition is unacceptable, full stop — not "slightly worse").

**F5. The direction banner marks world-coordinate axes only, never "front/back/left/right/top view".**
Which axis points up and which face is front depends on the modeler's coordinate conventions and is unknowable to this project. Marking world-axis directions + the three-colored arrow indicator is objective fact; conjuring a "front view" is speculation. The axes' on-screen directions are derived from actual `getPointOnScreen` projection, same source as the red-rectangle coordinates, so "the label says one direction while the picture shows another" cannot happen.

**F6. Magenta pixel statistics must complete before the banner is added.** The banner adds `banner_h` to the image height (proportional to image width); counting after adding it misaligns the red-rectangle coordinates from pixel coordinates wholesale. Likewise, any code that reads the post-banner image and compares against `bbox_2d` (e.g. verification scripts) must shift the y coordinate down by the manifest's `banner_h` field — **do not hard-code a constant**; the banner height is computed proportionally from image width.

**F7. In-image font sizes and stroke widths must be derived proportionally from image width; never hard-code pixels.**
In the PPT each of the three images spans only about 4 inches of width; a 1827px image displays at about 450 DPI. The same disease was hit twice:
- Font sizes: the first version hard-coded fixed pixels (title 40px / subline 27px / axis labels 24px), which displayed at only **6.4pt / 4.3pt / 3.9pt** — user feedback: "too small to read". Now defined as ratios of image width (`BANNER_TITLE_RATIO=0.062` ≈ 18pt, `BANNER_SUB_RATIO=0.042` ≈ 12pt).
- Rectangle stroke: `RECT_WIDTH=3` hard-coded came out at only **0.48pt** — user feedback: "the line is too thin to see". Now `RECT_WIDTH_RATIO=0.0069` → 12px → **1.91pt**. The stroke expands **outward** (`x1-offset`), so thickening occupies only the outer white margin and never covers the diff highlight itself.

Nothing mismatches under changed resolutions either. The verification script has hard assertions for both (title ≥12pt, subline ≥8pt, stroke ≥1.5pt), so ratio changes cannot silently degrade.

**F8. Banner titles must be short, and overflow must be truncated.** With the enlarged font, long titles push against the coordinate-axis indicator on the right and squeeze the axis labels out of frame (measured: "old-revision V4 closeup · viewpoint isometric (same as new)" squeezed out the X-axis label). `add_direction_banner()` now truncates to the available width with an ellipsis. Information such as "same camera as the new revision" is already expressed by the two images' identical view-direction vectors; it need not be written into the title.

### G. PPT layout layer (`build_pptx.py`)

**G1. Layouts must be eyeballed via PowerPoint-exported images; python-pptx coordinate numbers alone are not enough.**
Coordinates being right does not mean the appearance is right. All three problems this round were found only via exported images: the legend row cut off in the lower half, long right-column text squeezing the legend off the slide, and the slide caption duplicating the in-image direction banner. Export method (this machine has PowerPoint, no LibreOffice):

```python
app = win32com.client.Dispatch("PowerPoint.Application")   # requires pip install pywin32
pres = app.Presentations.Open(abs_path, WithWindow=False, ReadOnly=True)
for i, s in enumerate(pres.Slides, 1):
    s.Export(os.path.join(outdir, f"page{i}.png"), "PNG", 1920, 1080)
```

**G2. Every font size must be set explicitly with `Pt()`.** The blank layout `slide_layouts[6]` defaults to 18pt; inheritance makes the info band oversized and pushes it off the slide. The verification script has a hard assertion against "runs without an explicit font size".

**G3. Color and weight splits must be set at run level.** Paragraph-level settings unify the whole paragraph and cannot produce "grey labels + black bold values" (`put_runs()` encapsulates this).

**G4. The info band height must be reserved for the slide with the most lines, not for the current slide.** It evolved through two rounds: with `BAND_H=1280000`, slide 1 (longer right-column text) had its legend row squeezed below the slide's bottom edge → 1620000 fit all three slides (the left column was a constant 3 lines back then); once per-cluster sizes were added, the left column of a multi-diff slide became `1 (of N diffs in total) + N (per-cluster details) + 2 (added/removed)` lines — totaling 5 at N=2 — and at 1620000 the "added material" line landed right on the legend (confirmed via exported image), hence **1780000**. Line height is estimated as 12.5pt × line_spacing 1.15 + 5pt space_after ≈ 246062 EMU per line. The legend row reserves `LEGEND_H` separately. Before raising the height you must verify the images are not constrained by column width: the triptych's aspect 1.10 > `col_w/img_area_h`, so the displayed image width is always `col_w` (4.043in); as long as `BAND_H` stays under about 1.97M it affects neither the image-area size nor the pt conversion of stroke widths.

**G5. Do not draw a full-width divider line under the title bar.** A thin rectangle was once drawn at `MARGIN_EMU+TITLE_BAR_H-150000` as a visual trim; that position lands exactly on the lower edge of the title's second line ("part volume / change amount") and covered the text (user feedback). The title's trim is now carried by the part name's own underline — zero extra vertical space and no chance of covering other text. Any change that "draws decorative blocks/lines near text" must be exported to an image to confirm it does not cover text; correct coordinates do not imply no overlap.

**G6. The PPT must not repeat information the in-image banner already carries.** The image itself brings "old-revision V4 closeup + view-direction vector"; adding a PPT line "old-revision V4 · closeup" is a repeat, and in exported images the two text blocks stack tightly and look wordy. The standalone caption row was removed (`CAPTION_H=0`), giving that height to the image area.

**G7. The left column is narrow (33% of the layout); multi-item content must take one paragraph per item and never be crammed into a single line.** The first draft of per-cluster sizes was a single line `① 1.29×10.97×1.23   ② 0.51×3.01×0.56 mm`; measured, the wrap point fell mid-way through "② 0.51×...", leaving the circled digit stranded at the end of the previous line, impossible for readers to map back to its size. Changed to one paragraph per cluster (indented bullet + `① added  1.29 × 10.97 × 1.23 mm`). By the same token, **measure before lengthening the legend-row text**: after changing "red rectangle = auxiliary locator for diff positions" to "red rectangle = each diff located separately (①②… match the left-column size list)", the whole row wrapped into two lines and the trailing "(X red/Y green/Z blue)" overflowed and got clipped.

### H. Framing multiple diff points separately

**H1. A symmetric difference can be composed of several spatially separated pieces of material; drawing the union bbox degenerates into "a giant rectangle around the whole part".**
Measured, the three diffs split by `Shape.Solids`: PART-C removed = 2 pieces, PART-D added = 1 piece, PART-A added = 2 pieces. PART-A's two pieces have centers at x = +17.89 and −0.38 (18.3mm apart) while the part is only about 19mm wide, so the union bbox of 19.17×10.97×3.21mm exactly equals the whole part — **the second piece that stretched it is only 0.76mm³, 16% of that diff's volume**. This is the root cause of the user feedback "two changes circled together; the circle loses its meaning".
**Fix**: `split_clusters()` splits the `removed`/`added` Solids → `project_clusters()` projects per cluster → `merge_rects()` merges overlaps → `draw_rects_on_image()` strokes each rectangle and marks the circled digit outside it (leader-line annotation; see H7). After the fix, the sum of PART-A's two rectangle areas is only **0.231** of the union.

**H2. Cluster splitting must happen in the precompute phase (`.Solids` is a geometry read, subject to A1).** Moving it into the render loop re-triggers A1: once the `App::Part` container's `.Shape` is stripped by a visibility write, `.Solids` goes with it, the cluster count silently becomes 0, it degenerates to a single rectangle, and no error is reported.

**H3. Clusters separated in 3D can overlap in 2D; merging must happen after projection, and each image computes its own.**
PART-C's two clusters are only about 2mm apart in center; their projection on the closeup necessarily overlaps, and two interleaving rectangles look worse than one (measured for that part: merged = 1 rectangle is the correct result, not a splitting failure). But the overlap relation depends on the camera, and the overview's camera differs from the closeup's — so it **cannot be computed once and reused everywhere**. The sole exception is the old-revision closeup — it must reuse the new-revision closeup's rect list (pitfall C2: the two images achieve pixel-perfect alignment via same camera + same coordinates).

**H4. `boxZoom` framing and `score_direction` scoring still use the union bbox; do not casually rework them into multiple rectangles.**
Switching framing to a single cluster would make the new/old closeups cover inconsistent regions, break C2's alignment, and drop the other cluster; switching scoring to multiple rectangles would change the overview's viewpoint selection and misalign all measured baselines. The `bbox_2d`/`bbox_2d_closeup` fields therefore keep their union semantics.

**H5. Boolean fragments must be filtered, but limit truncation must be printed.** `CLUSTER_MIN_VOLUME=0.001` (same value as `geom_diff.VOLUME_THRESHOLD`) filters near-zero fragments; `MAX_RECTS=8` caps the rectangle count — past the cap, keep the top N by area and `print` the dropped cluster numbers; silent truncation would turn "full rectangle coverage" into an illusion.

**H6. Single-rectangle images carry no circled digit.** A lone "①" on the image carries no information; the PPT left column stays in sync: a single cluster gives the size directly, and "N in total" plus circled digits appear only with multiple clusters. The circled-digit character sequences on both sides must match (`render_diff.CIRCLED_DIGITS` vs `build_pptx.CIRCLED`), otherwise readers cannot map the list onto the rectangles.

**H7. The circled digit must be placed outside the rectangle, tied back to the rectangle edge by a leader line, with the landing point chosen by measurement.**
The first version drew the red-background white-text label at the top-left corner **inside** the rectangle; on small rectangles the label nearly filled the whole box and covered the diff itself (user feedback). It now places the label outside the rectangle plus one thin leader line back to an anchor on the rectangle edge (`_place_label` / `_leader_cost`). Three measured conclusions:
- **The landing point must be measured per image; it cannot be pinned to a fixed corner**: which side of the rectangle is blank depends entirely on the model's shape and position at that viewpoint. The approach: sample the non-white pixel share at eight candidate spots around the rectangle.
- **Scoring must weigh both "landing-spot emptiness" and "leader-line crossing cost"**. Judging landing spots alone, measurement picked an empty corner at the image edge, so the leader line crossed the part body over a long stretch — uglier than a label hugging the rectangle edge; the path alone could press the label onto content. The score is `emptiness − crossing cost`.
- **Labels use red text on white, not white text on red**: over open space outside the part, a solid red block draws far more attention than a thin border, pulling it away from the magenta highlight.

**H8. Labels must be drawn in a separate pass after all rectangles are stroked.** Drawn in the same loop as the rectangles, an earlier label gets treated as "content" by the emptiness evaluation of later candidate spots (while earlier rectangles cannot affect already-decided landing points); only the two-pass split lets the evaluation see the complete rectangle layout.

### I. Generalization refactor (P0, 2026-08-27)

**I1. A face-count gate cannot stop a boolean hang; a subprocess hard timeout is required.**
Measured: `PART-B` has only **912 faces** (far below `MAX_FACES_FOR_BOOLEAN=5000`) yet one `cut` ran **840s without returning** — face count does not correlate with boolean cost. The old dodge was hard-coding part names in `SKIP_BASE_NAMES`, which broke on any other assembly, with the symptom of **the whole pipeline hanging indefinitely, no output and no error**. See §2's subprocess note. The face-count gate is kept, but only as a cheap up-front block (measured to stop the 8528-face top-level assembly pseudo-candidate — that face count is the Windows-side measurement; the same part is 8553 faces on Linux, see §4 — which was previously dropped silently).

**I2. For translation-type diffs (`moved`) the highlight body coincides exactly with the part body; the part body must be hidden.**
`moved` has no symmetric-difference geometry, so the highlight body is the part's own Shape. If the part is also visible (`TARGET_TRANSPARENCY` semi-transparent), the blended color washes the magenta down into the low-saturation band (g in 60~110), `score_direction`'s `sat_in` stays 0, and **every run is misjudged as fallback**, producing an image annotated "occluded" although actually clearly visible. **The fingerprint is `sat_in == 0` while `magenta_px_overview` is large** (measured 0 vs 17287); the two numbers contradicting each other is this very disease. Fix: in both the overview and the new-position image, `render_moved_one` sets the `self_names` subtree invisible and leaves only the highlight body. `shape_changed` is unaffected — there the highlight body is a thin-plate-shaped symmetric difference that does not coincide with the part surface.

**I3. The `moved` old/new images must each frame their own bbox.**
When both images shared the new-position bbox, the old-position image's rectangle landed **beside** the magenta part (caught by eyeballing a PowerPoint-exported image — yet another confirmation of pitfall G1: coordinates being right does not mean the picture is right). `geom_diff` therefore records both `bbox` (new) and `bbox_old` (old) for `moved`. Note both images still share one camera and their projections finish in one batch — **no camera operation may be inserted** for this (pitfall C2). The overview likewise frames both the old and the new position, otherwise readers cannot tell "where it moved from".

**I4. The global alignment check must not use "the overall bbox of all geometry across both revisions".**
That misjudges "parts added/removed" as "a global coordinate-system translation": measured, this case's baseline (known well-aligned) was judged misaligned (center shift 1.24mm, overall size difference 5.41%); the root cause was one extra anonymous object `SOLID003` in the new revision (X[-10.15, 10.15], beyond the old range) stretching the bbox. The correct criterion is **paired parts' shift vectors being large enough and directionally consistent**: a moved global coordinate system → all parts shift in the same direction by the same amount; only a few parts changed → shifts scattered, mostly 0. Measured three-tier comparison: baseline 61 pairs (median 0mm) → no alarm; synthetic 5mm global translation → alarm; only 2 parts moved 5mm each while the other 38 stayed → no alarm (consistency 0.707 < 0.8).

### J. Real public-corpus testing (2026-10-04)

First controlled ground-truth verification on **real public corpora** (the 8-solid-part mechanical assembly at GitHub `XRobots/openDogV3`); it exposed two pitfalls synthetic fixtures cannot detect.

**J1. Name folding merges "different parts sharing a prefix" into one candidate, which the container rule then swallows whole — a ground-truth translation is reported as "no diff" with exit code 0.**
Part names look like `openDog V3_internals_toleranced v001/v002/.../v11` (generated at FreeCAD/OCCT import); `base_name` folds all 8 **distinct parts** into one key. The geometry layer's container rule is "a child key hitting the candidate set means container"; after folding, the container's child key **equals the container's own key** → **self-hit** → the entire group, container included, is recorded into `skipped_parts` and none of the 8 parts gets compared. This is the deadliest false negative for a CI gate: the gate passes the change it should have blocked.
Fix (both layers changed together; neither alone suffices): ① `bom_diff.align_keys` decides folding per **family** — when the family's name set is identical across both revisions, use the exact name (zero ambiguity when names are stable), and fold only otherwise (preserving the Creo instance-number drift scenario); the keys go into `bom_diff.json`'s `old_key_map`/`new_key_map`, the geometry layer reads that table, **removing the duplicated `base_name` source of truth**. ② The container rule gains a `k != bn` guard to prevent another self-hit inside a folded family.
**Do not retry this dead end**: changing the container rule alone to judge by object type (`App::Part` + non-empty `Group`) is insufficient — folding makes a container and a part share the same key, and a group-level decision cannot split them; measured, the container re-enters the comparison and double-counts (ground truth of 1 reported as 2 — a D-015 regression).

**J2. OCCT booleans silently return degenerate results between "two nearly fully coincident complex STEP shapes".**
Measured on two versions of the same part (28 faces, volume difference 10.4mm³ — the same even with 50% of the volume cut off): `common` returned **-265.07** (negative volume), `fuse` produced an empty shape with bbox **±DBL_MAX**, and `cut` returned a shape larger than the original (21188 > 20923). The same two parts' shapes are themselves fine (`common(self,self)` is fine, booleans against a 0.5mm-translated copy are fine, booleans against an unrelated part are fine) — **degeneration happens only in the near-coincidence tier**. Synthetic fixtures (`Part.makeBox` natively constructed) are unaffected, which is why this stayed hidden so far.
**Current handling (implemented)**: two layers.
① **Honesty**: `boolean_worker.py` detects the degenerate result (`diff.isNull()` or non-finite bbox edge lengths) and returns `ok:false, error:"boolean_no_result"`; the parent records it into `skipped_parts` and prints — "could not compute" is no longer silently read as "no diff".
② **Degraded verdict (no diff is lost)**: when the boolean fails but the **volume difference already exceeds tolerance**, `geom_diff` still reports `shape_changed`, with `highlight_mode:"whole_part"` and a `degraded_reason`; `render_diff.precompute_shapes` then treats the **part bodies of both revisions** as `removed`/`added` (the semantics hold: the old part body no longer exists as-is in the new revision, and the new part body is "the new shape"), so the normal `render_one` path runs and the **whole part** is highlighted. `degraded_reason` is carried all the way into `summary.parts_with_diff` and the report (`report.field.highlight` = "whole part — …").
Degradation only when the volume difference exceeds tolerance: a pure bbox change (e.g. a rotation) has no volume evidence; that case is still recorded as "could not compute" — **no guessing**. The cost: in a degraded entry, `removed_volume`/`added_volume` equals the whole part's volume rather than the real added/removed material — the degraded marker exists precisely so readers know this.
⚠️ The check must use **`XLength`** (= XMax−XMin = inf), not merely whether `XMin`/`XMax` are finite: a degenerate bbox's endpoints are ±DBL_MAX, which are **finite values**, so endpoint checks cannot stop it (bitten once: the first version of the fix still passed silently in measurement).

**J3. After proximity pairing sieves a pair with ">50% volume difference" into double-sided orphans, the new-side orphan is never collected and the old-side orphan never enters the contract — a real change inside an equal-count same-name family gets reported as "no diff" with exit code 0.**
Caught by the 2026-10-05 gate1 corpus measurement (24 pairs of public assembly revisions): among 6 same-named product `SOLID`s (SolidWorks default names; document Labels uniquified into SOLID..SOLID005), one part's volume went **20548→9090mm³** (confirmed by independently loading both revisions into FreeCAD); the volume sieve turned this pair into double-sided orphans — the old side was recorded into `unpaired_old_labels`, the new side was **silently dropped**, and `unpaired_old_labels` lived only in the intermediate artifact `geom_diff.json`: `build_summary` projects only entries carrying notes and `render_diff` only collects entries carrying `geometric_changes`, so the final manifest and report had **zero trace**. Same family as J1/J2: the CI gate passes real changes.
Fix (implemented): ① `pair_by_proximity` **returns both sides' orphans symmetrically**; shape-less objects (isNull) are carried out with the return value and recorded by the caller into `skipped_parts` as `no_shape` (sealing another silent channel); ② double-sided orphans are **paired 1:1 locally in place** and reported as a degraded `shape_changed` (`highlight_mode="whole_part"` + `degraded_reason="unpaired_after_proximity"`, with `volume_delta=|Δvolume|` — the boolean never ran; this is not a symmetric difference), while a single-side leftover orphan is reported on its own side (the missing side's label/volume/bbox_old are None, and **`bbox` always comes from the side that exists** — the render layer indexes `bbox`/`instance_index` directly; a missing key means KeyError → exit 2); ③ orphan-pair shifts **do not enter `pair_shifts`** (local pairing does not claim "the same part" and is not fed into the alignment-consistency criterion).
Invariant (under the stated condition): when both sides have **equal counts** after the shape-less filter, the double-sided orphan counts are equal (consumed one-to-one); single-sided orphans appear only when the filtered counts differ. Orphan pairing **applies no** volume sieve — the orphans are exactly what the sieve left behind; reapplying it would empty both sides.

**I5. Substring assertions must guard against false failure from "longer numbers".**
The verification script checks `"0.00 mm³" not in text` to confirm "no misleading zero added/removed lines"; a `800.00 mm³` (a part volume) matches it and causes a false failure (hit in measured practice this round). Changed to exact matching of field labels (`"removed material" not in text`). Same class: any assertion matching numbers by substring must first ask "is there a longer number containing it?"

---

## 6. Troubleshooting Methodology (worth reusing even more than the conclusions)

Problems of the "code raises no error, output is wrong" kind cannot be located by reading code or eyeballing screenshots. The techniques verified effective in this project:

1. **Measured pixels first; never conclude by inference.** Count the actual magenta pixel extents in the screenshots and compare one by one against the projected coordinates computed in code. Several pitfalls share the same surface symptom ("the rectangle/highlight is misplaced"); only quantified comparison can separate them.
2. **A diagnostic script must reproduce the full context, otherwise its conclusions are untrustworthy.** This project paid the price twice: an isolated script that "tested only round 3" reached the wrong conclusion "everything is fine" — it skipped the first two rounds' visibility writes, conveniently bypassing A1; a script that used only `fitAll` framing to validate the coordinate system reached the wrong conclusion "y is not flipped" — under centered `fitAll` framing the values are nearly identical before and after the flip; only `boxZoom` off-center framing exposes B1. **"The isolated test passes, the full pipeline fails" is itself a strong signal**: the problem is in the side effects of preceding steps, not in the code under test.
3. **The toggle method attributes pixels.** Hide/show a single highlight body and pixel-diff the before/after images to directly determine which cluster of pixels belongs to whom. Far more reliable than "it looks like residue".
4. **Multi-tier comparison classifies the error.** Measure the same state under several framing tiers: overshoot **scales proportionally** with zoom → coordinate-scaling problem; **constant offset** → origin problem; **one side only** → origin flip. This discrimination routine pinned down B1 directly.
5. **Distinguish true highlight from blended-color artifacts.** A true highlight body is fully saturated magenta `(203,17,215)`/`(219,17,218)`; the blend showing through semi-transparent parts is a low-saturation purple `(182,112,205)`. The latter can be pushed through the color threshold by LANCZOS upscaling, conjuring an extra cluster of "outside-rectangle magenta" out of thin air. **Attribute pixels on the natively rendered image, not on the upscaled one.**
6. **Persist self-check numbers into the artifacts.** `render_manifest.json` records each image's `magenta_px_*`, making anomalies visible at a glance and saving you from rewriting diagnostic scripts every time. A1 and C1 were both "silent failures with no error"; eyeballing images missed them for an entire round.

---

## 7. Known Limitations (deliberate PoC-stage trade-offs, not bugs)

- `PART-B`'s boolean does not finish: measured **912 faces** only, yet a single `cut` exceeded 840s without returning (face count does not correlate with cost). It is now interrupted by the subprocess hard timeout at `BOOLEAN_TIMEOUT_S` (default 60s), recorded into `skipped_parts` with `reason:"timeout"`, and explicitly listed in the artifacts (report); **no more hangs and no more silent skips** — but that part's geometric diff still cannot be detected. To detect it, raise `--boolean-timeout` and accept the corresponding runtime.
- The top-level assembly `ASSY-TOP_1_1_ASM_1_A_ASM` (measured 8528 faces on Windows; 8553 faces for the same part on Linux) appears as a pseudo-candidate. It is not a real part; skipping it is the correct behavior — it was previously silently dropped, now it is visible.
  **The criterion for skipping it is "assembly container", not the face-count gate**: STEP import builds the top-level assembly as an `App::Part` container, and a container's "shape" is the union of all its children — any child change would make the container **report the diff one extra time** (measured: a 3-part synthetic fixture got reported as 3 diffs, ground truth 2). The current rule: if a candidate of this comparison is among the container's children, record it into `skipped_parts` with `reason:"assembly_container"` and skip it; **an empty container with no children is still handled as an ordinary part**, otherwise real parts would be silently missed. The face-count gate (`--max-faces`, default 5000) remains in place, to block pseudo-candidates that are **not containers** yet have huge face counts.
- **The top-level assembly's product name participates in the BOM comparison**: STEP export writes the document name into the top-level `PRODUCT` record, and the BOM layer compares by `PRODUCT` name, so "renaming the assembly" is faithfully reported as one added + one removed. For real CAD exports (top-level product name = assembly name, stable across revisions) this usually has no effect, but a rename is a rename; the tool does not hide it.
- `PART-E` failed to match a geometry object: **the real reason is that FreeCAD imported a nameless shape as an anonymous Label** (`COMPOUND`/`COMPOUND001`, with parent `COMPOUND002`); it has nothing to do with naming habits, so `base_name` cannot match. P1's `resolve_geometry_object` (walking back up the anonymous object's parent Label) will remove this item; for now it is still explicitly reported as `note: "no geometry match"`, never silently missed.
- Occlusion is decided by a coarse bbox test (not triangle-mesh raycast), and a single diff cluster's red rectangle remains looser than its actual 2D silhouette (under an isometric viewpoint the 3D bbox projection is naturally larger than the visible silhouette). Per-cluster rectangles (H1) solve "many diffs enclosed by one giant rectangle"; they do not change this looseness of a single rectangle.
- Rendering depends on FreeCAD's full GUI mode (`showMainWindow()`); a window briefly pops up at runtime — it is not fully headless. `setupWithoutGUI()` was tried; it cannot create ActiveDocument/ActiveView and is unusable.
- **Diffs completely buried inside the assembly still show no highlight in the overview** (measured on PART-C: even with non-target parts at 85 transparency, only one of the 26 directions can see it — an orthographic side view whose picture is a smeared wireframe blob). The code then takes the fallback, picks the clearest picture, locates purely via red rectangles, and explains the reason in the report and PPT text bands. This is a trade-off between "a locating image you can understand" and "an image with highlight you cannot understand", not a bug.
- A pure-removed part has no magenta on the new-revision closeup (the material exists only in the old revision). If a human finds this insufficiently intuitive, a next step could overlay a semi-transparent "original material position" ghost on the new-revision image.
- Viewpoint scanning grew the whole pipeline from about 210s to about 355s (rendering 197s: 26 extra directions scanned per diff at about 0.85s each; geometry 155s, of which 62s is PART-B's boolean timeout wait).
- `moved` diffs report only the translation and the rotation angle, **with no judgment of "along which path"**, and do not recognize the case "a part replaced by a different part of equal volume" (when volume and bbox both match, it is classified `moved`). The criteria are deliberately strict; a misjudgment falls onto the safe side `shape_changed`.
- The `moved` rotation angle prefers `Shape.Placement.Rotation`; when unavailable it degrades to "the multiset of bbox edge lengths equal but the permutation different", which is a **sufficient-but-not-necessary** criterion (insensitive to rotations that are not integer multiples of 90°); the artifact then carries `rotation_detected_by: "bbox_permutation"`, and the report and PPT both note "low confidence".
- **BOM-layer rename drift (the main noise source measured on the gate1 corpus)**: when the same part is renamed across revisions (manual rename / exporter auto-names generated from timestamps / case-only differences), the BOM layer faithfully reports one added + one removed — this is the correct report for "the name really changed", not a fabrication; but the geometry layer cannot pair across names, so the tool cannot use shape evidence to fold both sides into a single "rename". Among the gate1 corpus's 24 pairs, 7 structurally unchanged revisions therefore reported diffs (all showing the added==removed symmetric signature; in the most extreme sample every product name was a timestamp, and the two revisions' 190 names were completely different). For a CI gate this is a known noise type — when reading results, first check whether the added/removed lists correspond symmetrically. v0.2 direction: shape-fingerprint matching between the two sides' candidates to fold a "rename" into one entry.
- **Native crash on giant STEP (exit 2, honest failure)**: a 38.5MB STEP triggers a FreeCAD/OCCT native access violation in the `Import.insert` stage (measured on the gate1 corpus; the reproducing pair is recorded). A native crash cannot be caught at the Python layer and is **never folded into "no diff"** — exit code 2, and the caller handles it as "execution failure"; the same input crashes again on retry, so switch inputs or await root-cause isolation (file contents vs parser).
- The circled-digit leader line's landing point is picked among eight candidate spots around the rectangle, with no global layout optimization. With many rectangles (>3) close together, later labels only avoid "overlapping already-placed labels" and do not backtrack or rearrange earlier ones, so an individual case with an unusually long leader line can appear. Measured: this assembly has at most 2 rectangles, and the case never arose.

---

## 8. Artifact Field Contract (`render_manifest.json` — the consumption contract of `report.html` / `report.md` / `compare.pptx`; changing either side requires syncing the other)

```
base_name, instance_index,
change_type,                                   # "shape_changed" | "moved"; downstream must check it first
label_old, label_new,                          # revision labels (shared by the in-image banner and by downstream (report / PPT))
overview, overview_rect, closeup_new, closeup_new_rect, closeup_old, closeup_old_rect,
bbox_2d, bbox_2d_closeup,                      # the union rectangle over all diff clusters; still used for framing and overflow-type assertions
rects_overview, rects_closeup,                 # per-cluster rectangles (after overlap merging), [{rect:[x1,y1,x2,y2], labels:[1,2]}]
                                               # coordinates are pixel coordinates on the upscaled image, PADDING_PX already included
cluster_count, cluster_details, rect_line_width_px,
old_volume, new_volume, volume_delta, volume_delta_pct, diff_bbox_size_mm,
removed_volume, added_volume,
magenta_px_overview, magenta_px_closeup_new, magenta_px_closeup_old,
overview_view_tag, overview_view_dir, overview_view_score, overview_view_fallback,
overview_view_readability, overview_ecc, overview_compose, overview_body_pct,
overview_sat_in, overview_sat_out, overview_axis_dirs,
closeup_view_dir, closeup_axis_dirs, banner_h

Only records with change_type == "moved" additionally have:
translation_mm, rotation_deg, rotation_detected_by, bbox_2d_closeup_old
```

Each `cluster_details` item: `{index, role("removed"/"added"), volume, size_mm}`, in descending volume order with `index` starting at 1, in the same order as the in-image circled digits and the report's left-column list.

**Value conventions for existing fields on `moved` records** (so downstream does not misread 0 as "no diff"): `cluster_details=[]`, `cluster_count=0`, `removed_volume=added_volume=0`, `volume_delta=volume_delta_pct=0`. Downstream **must** check `change_type` before reading these fields — an unconditional read renders "just moved position" as "no change at all". Likewise, downstream (report / PPT) shows no added/removed lines in the `moved` tier, and the PPT legend text differs (in those images magenta represents the part body, not changed material).

An empty manifest (`[]`) is not a failure: `diff_count=0` is a valid conclusion — only the "no geometric differences detected" explanation page is produced and the run ends with **exit 0**; the lists of skipped / unmatched / threshold-filtered parts come from `summary.skipped_parts` / `summary.unresolved_notes` / `summary.filtered_by_threshold`. A **missing manifest file** is the failure, recorded as **exit 2** (execution failure) per the outward contract.

---

## 9. CLI Interface Boundaries (`caddiff diff` / `caddiff difftool`; callers must read)

`caddiff diff` is the main entry: two STPs go in, and under `<out>` it produces `diff_manifest.json` (machine contract, includes `summary`) + `report.html` / `report.md` (human-readable report) + `images/*.png` (diff images); the intermediate artifacts `bom_diff.json` / `geom_diff.json` are kept, and with `--pptx` there is additionally `compare.pptx`. There is also the git-integration entry `caddiff difftool` (same options as `diff`, except `-o` defaults to a newly created temporary directory and the report path is printed): **it returns 0 even when differences are detected, and only 2 on execution failure** — `git difftool` treats a non-zero exit code from the tool as "external diff died" and aborts the whole diff, so `diff`, which must return 1 on detected diffs, cannot serve git; conversely **`difftool` must not be used as a CI gate** (it never returns non-zero because of detected diffs); in CI always use `caddiff diff`. For git-side configuration and usage see [`git-integration.md`](git-integration.md).

**Calling contract**
```bash
caddiff diff <OLD.stp> <NEW.stp> [options]

# positional arguments
  OLD.stp / NEW.stp     the two STEP/STP files

# options
  -o, --out DIR         output directory (default ./caddiff-out)
  --label-old NAME      old-revision label (default: derived from the filename)
  --label-new NAME      new-revision label (default: derived from the filename)
  --lang {en,zh}        artifact language, default en (English first)
  --pptx                additionally export the comparison PPT (not exported by default)
  --max-faces N         skip the boolean above this face count (default 5000)
  --boolean-timeout S   hard timeout seconds for a single boolean (default 60)
  --min-diff-pct P      diff volume must exceed this percentage of part volume (default 0 = disabled)
  --skip-parts A,B      skip the named parts (exact match on base_name)
  --version / --help
```

When the filenames are hashes you must pass `--label-old` / `--label-new` explicitly, otherwise the revision labels on the images and in the report are meaningless.

**Exit-code semantics (outward contract, usable directly as a CI gate; do not merely check whether an exception was thrown)**
- `0` = no differences between the two revisions. **Includes the "no geometric differences detected" tier** — the report is then a single explanation page and `render_manifest.json` is `[]`. **Callers must not treat it as failure**; it is a valid conclusion.
- `1` = diffs detected.
- `2` = execution failure (unreadable input / subprocess timeout or crash / internal error). ⚠️ **Any failing internal substep must propagate to 2**; it must not be folded into 0 or 1.

**Which field to read to learn "what this run produced"**

The `summary` block of `diff_manifest.json` is a **single entry point prepared for callers** — reading it is enough; there is no need to correlate other files (it only aggregates and never recomputes; every field comes from upstream artifacts, avoiding "summary and details disagreeing"):
```
summary.diff_count                    number of diffs; 0 means none detected (a valid conclusion, not a failure)
summary.by_change_type                {"shape_changed": N, "moved": M}
summary.parts_with_diff[]             one per diff: base_name + change_type;
                                      shape_changed carries volume / share / added-removed / cluster count,
                                      moved carries translation_mm/rotation_deg
summary.skipped_parts[]               parts that did not participate + reason
                                      (timeout / too_complex / no_shape / user_skipped)
summary.unresolved_notes[]            the ones that could not be compared (e.g. no geometry match / count mismatch)
summary.filtered_by_threshold[]       filtered out by --min-diff-pct
summary.global_alignment_warning      true = the two revisions' global alignment looks inconsistent; differences may largely be spurious
summary.total_candidates              number of candidate parts compared
summary.settings                      the gate values actually in effect this run (for reproduction and tuning)
summary.reason                        present only when has_differences=false;
                                      the valid-conclusion statement explaining "no differences between the two revisions"
```

**The three in-between `skipped/unresolved/filtered` groups are "honesty" fields: not surfacing them to the user amounts to teaching readers to equate "not mentioned" with "no diff".** Per-diff image paths and pixel self-check counts remain in `render_manifest.json`; read it only when attaching images or running checks.

`build_summary()` is safe against missing/corrupted artifacts (it returns an empty structure with `diff_count=0` instead of throwing), but **success or failure must not be inferred from it** — read the exit code for that.

**Runtime environment constraints**
- **A single run takes about 355~366s** (measured: BOM 0s / geometry 155~167s / rendering 197~198s / PPT 1s); it must run as an **async task or a long-timeout CI step**, never hung on a synchronous request. The runtime grows with the diff count (rendering is about 22s per diff: 26 candidate viewpoints × 0.85s); set rendering timeouts by estimating from the diff count rather than hard-coding.
- **Rendering depends on FreeCAD's full GUI mode** (`showMainWindow()`); a window briefly pops up, so a virtual display (Xvfb or similar) is needed, or accept the popup. `setupWithoutGUI()` was tried and is unusable (cannot create ActiveDocument/ActiveView).
- **The boolean forks one more subprocess layer** (`boolean_worker.py`); the container must allow creating subprocesses and provide a writable `tempfile` directory (for BREP flushing; measured at about 2.5MB for the largest single part).
- **The container image must install `x11-utils` (which provides `xdpyinfo`)**: `deploy/docker-entrypoint.sh` provides the virtual display via `xvfb-run`, and `xvfb-run` uses `xdpyinfo` polling to decide whether the X server is ready — without it, Xvfb starts but python is **never launched**; the symptom is container CPU 0%, zero output, never exiting (measured: hit on the very first real run of the image; `docker logs` showed 0 lines the whole way, with only `sh` and `Xvfb` in `ps`). `apt install xvfb` does **not** pull in this dependency; it must be installed explicitly.
- CJK fonts: without CJK fonts in the container, Chinese text on the images turns into tofu blocks. `FONT_CANDIDATES` already covers common Linux paths (Noto CJK / wqy); when none hits, a prominent warning is printed — **at deployment, treat this warning as a failure signal**, do not ignore it (otherwise the output is an "intelligible but textless" image).
- **Concurrency unverified**: written under the single-process single-GUI assumption; whether multiple instances on the same machine interfere with each other (ActiveView contention, temp-directory conflicts) has **not been measured**; deploy behind a serial queue first.

---

## 10. Files Involved

- `caddiff/bom_diff.py` — BOM diff (system Python; pure-text parsing of the STEP `PRODUCT` entities)
- `caddiff/geom_diff.py` — geometric diff (FreeCAD Python, console mode). `classify_change()` is the three-way classification (identical/moved/shape_changed); `placement_delta()` computes translation and rotation; `symmetric_diff_isolated()` pushes the boolean into a subprocess with a hard timeout (pitfall I1); `check_global_alignment()` judges global alignment from paired parts' shift consistency (pitfall I4). Output includes `change_type`/`skipped_parts`/`filtered_by_threshold`/`global_alignment`/`settings`
- `caddiff/boolean_worker.py` — boolean symmetric difference inside the subprocess (FreeCAD Python). **Must `import FreeCAD` before `import Part`**, otherwise a 0xC0000005 crash (pitfall I1)
- `caddiff/render_diff.py` — render + red rectangles + magenta highlight + old/new side-by-side + direction banner (FreeCAD Python, GUI mode). **The three-phase order of `run()` is a hard constraint** (pitfall A1); `capture_and_project()` guarantees screenshot and projection share one source (pitfalls B1/B2); `pick_best_direction()`/`score_direction()` are the overview's automatic viewpoint selection (pitfalls F1~F4); `add_direction_banner()` draws the direction banner (pitfalls F5/F6); `split_clusters()`/`project_clusters()`/`merge_rects()`/`draw_rects_on_image()` are the per-diff-cluster rectangle framing (pitfalls H1~H6); `_place_label()`/`_leader_cost()`/`_region_emptiness()` are the outside-rectangle circled-digit leader-line annotation with measured landing-point selection (pitfalls H7/H8); `render_moved_one()`/`draw_move_arrow()` render translation-type diffs (pitfalls I2/I3); `derive_label()` derives version labels; `write_manifest()` atomically flushes to disk. The output directory is passed in by the caller (`caddiff diff` passes `<out>/images`)
- `caddiff/report.py` — diff report generation (system Python, pure standard library): reads `diff_manifest.json` plus the sibling `images/render_manifest.json` (to get per-diff image filenames and numbers), emits `report.html` and `report.md`. **The report being a standalone module and the PPT becoming optional is decision D-006**: HTML/Markdown are the default artifacts — Markdown pastes directly into a PR and HTML reads directly in a browser, while PPT requires readers to install PowerPoint. **Deliberately no template engine**: the report is a one-shot artifact for humans; pulling in a Jinja2 dependency for it is not worth it — `html.escape` + f-strings suffice. **Do not read `geom_diff.json` to re-derive conclusions** — that would give the report and the manifest two separate versions of the truth
- `caddiff/build_pptx.py` — PPT assembly (system Python): title bar + three images in a row + two-column structured info band + legend (layout constraints in pitfalls G1~G7). `cluster_size_lines()` lists sizes per cluster; the circled digits `CIRCLED` must match render_diff (pitfall H6); dispatches the shape_changed / moved layouts by `change_type`; `build_empty_slide()` produces the "no diffs detected" explanation page (including the skipped-parts list); invoked only with `--pptx`
- `caddiff/run_pipeline.py` — orchestration entry (the pipeline behind the `caddiff diff` subcommand); note that step 3 takes both `stp_old`+`stp_new` paths. Exceptions and timeouts print the paths of the intermediates already produced; rendering timeouts are estimated from the diff count rather than hard-coded; `build_summary()` aggregates the conclusions scattered across several JSONs into the `summary` block of `diff_manifest.json` for single-point consumption by callers (see §9)
- `caddiff/cli.py` — the command-line entry (argparse) and **the sole implementation site of the exit-code contract**: orchestration-layer failures always raise `PipelineError`, translated here into 2, strictly separated from "diffs detected = 1" (previously any failing step did `sys.exit(1)`, so "FreeCAD crashed" and "the two revisions genuinely differ" looked identical in CI — the gate would pass commits it should have blocked and block commits it should have passed); argparse's own errors are also 2, consistent with this contract
- `caddiff/version.py` — the **single source of the version number** (`caddiff --version` reads from here; `pyproject.toml` uses `dynamic = ["version"]` + `version = { attr = "caddiff.version.__version__" }` and reads from here too). **Do not write the version number into any other file** — duplicate copies inevitably drift, and version drift breaks the compliance requirement "image tag ↔ source tag ↔ build script correspondence" (the image tag must correspond one-to-one with the source tag and `deploy/Dockerfile`; see [`../THIRD_PARTY_LICENSES.md`](../THIRD_PARTY_LICENSES.md))
- `caddiff/__init__.py` — package entry. Its docstring records that **flat imports are deliberate** (`import fcenv`, not `from . import fcenv`): the geometry/rendering modules must be launched by the **FreeCAD-bundled interpreter** as "standalone scripts"; that interpreter does not have this package installed, so relative imports would fail immediately; the entry (`cli.py`) is responsible for putting the package directory onto `sys.path`, so flat imports work both when "running from source" and when "pip-installed"
- `caddiff/make_moved_fixture.py` — generates the synthetic STP fixture of the `moved` type (FreeCAD Python). Real STPs contain no translation-type diffs; this is the only end-to-end path that exercises that branch
- `caddiff/fcenv.py` — the **single source of truth** for FreeCAD interpreter/library path resolution (`FREECAD_PYTHON` → `FREECAD_HOME` → common Linux install paths → `PATH`; raises with repair guidance when nothing is found, **no silent fallback to the current interpreter** — a silent fallback would only blow up at `import FreeCAD`, with the error point far from the real cause)
- `caddiff/console.py` — console encoding fallback: FreeCAD's bundled Python 3.11 has a GBK stdout on Chinese Windows; characters like `mm³` make `print` throw UnicodeEncodeError and kill the script with exit code 1 (measured: the STP had already been written correctly, and the crash happened only on the final print). A tty keeps the native encoding and only downgrades non-encodable characters to `?`; pipes/redirection switch to UTF-8
- `caddiff/i18n.py` — the single entry `t(key, **kw)` for artifact wording (default `en`; `--lang zh` or the environment variable `CADDIFF_LANG`; when a key is missing it falls back to the default language, and if still missing returns the key as-is — missing translations are exposed explicitly in the artifacts). **Console progress output does not enter this table** (English only), and locale-neutral characters plus the `skipped_parts.reason` enum values are never translated
- `docs/pipeline.md` — this file: the module functional documentation of the diff capability line (records only "why" and "the pitfalls you would hit if this were not written down")
- Repository-level descriptive documents: [`../README.md`](../README.md) (outward usage and the first screen), [`../CONTRIBUTING.md`](../CONTRIBUTING.md) (contribution and verification requirements), [`../THIRD_PARTY_LICENSES.md`](../THIRD_PARTY_LICENSES.md) (licenses of third-party components inside the image)
