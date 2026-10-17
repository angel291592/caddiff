# Performance Summary (`caddiff diff --perf`) — Module Functional Documentation

English | [简体中文](performance.zh-CN.md)

> This file records **why** the performance summary is built the way it is, and **the pitfalls you
> would hit if this were not written down**. What the code does is readable from the code; the
> pipeline it plugs into is documented in [`pipeline.md`](pipeline.md) (§2 architecture, §3 how to
> run, §9 CLI contract).

---

## 0. What it is, and how to switch it on

`--perf` adds one step (Step 3/6) to `caddiff diff`: two revisions go in, and out come the
**solid volume**, the **volume-weighted centroid**, and the **volume inertia about the assembly
centroid**, plus the deltas between the revisions and a per-part allocation of the centroid shift.

| Option | Meaning |
|---|---|
| `--perf` | turn the performance step on (default: **off** — see §1) |
| `--perf-tol T` | relative tolerance for "did anything actually move" (default `1e-06`, multiplied by the assembly size) |
| `--perf-budget S` | time budget in seconds **per revision** for the probe (default `300`) |
| `--density SPEC` | a material density **you have confirmed**, e.g. `7.85 g/cm3`; without it no mass is computed at all |

Artifacts (only when `--perf` is on): `<out>/perf_snapshot.json` (raw per-revision numbers),
`<out>/perf_diff.json` (the comparison), and `summary.performance_diff` inside
`diff_manifest.json` — which is what the report reads.

**The performance block never changes the exit code.** The exit-code contract answers "is there a
difference?", and performance figures are a *derivative* of geometry/BOM changes: if volume or
centroid moved, the geometry layer has already reported a difference. Letting performance vote
would turn "someone changed a material parameter" into a red CI gate. `has_differences` is still
`bool(bom_diff_count or geometry_diff_count)`, character for character.

## 1. Why volume-based, and why no mass unless you say so

`ShapeMaterial.Density` is **not usable as a default**: measured across several real public STEP
assemblies, every object reports the FreeCAD Default `1e-09 kg/mm³` — the placeholder. Reading it
and multiplying would silently produce "steel parts measured as if they were water", which is the
exact class of failure this project treats as the worst kind (a plausible-looking wrong number).

So: **no `--density`, no kg anywhere.** `mass_kg_old` / `mass_kg_new` / `delta_mass_kg` stay
`null`, and the report shows the mandatory sentence *"Material density is not confirmed; no actual
mass was computed."* With `--density 7.85 g/cm3` the mass is `volume × density` with the density
recorded in `settings.density_source = "cli"` and `settings.density_kg_per_mm3`, so a reader can
always tell where the number came from. Accepted units: `kg/mm3` (also free of unit = same),
`g/cm3`, `kg/m3`; an unknown unit is a hard error (parameter error → exit 2), never a guess —
mixing up `g/cm3` and `kg/mm3` is a factor of 1e6.

## 2. Which numbers, and why exactly these

- **Volume-weighted centroid, not a centre of gravity.** Inside the tool every object carries unit
  density, so the centre is a *volume* centre. That is only the centre of gravity if the density
  is uniform, which the tool cannot verify. The report says so in words
  (*"Volume-weighted centre (uniform-density assumption), not a verified centre of gravity."*) —
  that sentence is mandatory, not decoration.
- **Inertia about the assembly centroid, not about the world origin.** Inertia about the origin
  depends on where the exporter happened to put the model origin; two revisions exported from
  different datums would show a huge fake change. Inertia about the assembly's own centroid is
  invariant under a global translation. The tensor is written in **world axes** — which is what
  `MatrixOfInertia` already gives you (pitfall F2 below).
- **Only six components plus principal moments; no "stability" and no "stress".** A 3D volume
  inertia (mm⁵) cannot stand in for a second moment of area (mm⁴), so nothing here may be called
  a buckling/slenderness risk; and an inertia figure is not a stiffness figure. Both disclaimers
  are printed with the numbers.
- **Principal moments come from the aggregated tensor**, never from summing per-part principal
  moments, and never from `PrincipalProperties.Moments` axis order (pitfall F5).
- **The contributor list is an exact allocation, not "share of the shift".** For every part label:
  `d_i = (V1_i·c1_i − V0_i·c0_i − Cb·(V1_i − V0_i)) / Vb`, with `Cb`/`Vb` the midpoints of the two
  revisions. The identity `Σ d_i == C1 − C0` holds exactly (asserted at runtime; a violation is
  reported as `degraded_reason`), added and removed parts are absorbed automatically, and terms may
  be negative — hence the mandatory note that it is a mathematical allocation, not a ranking of
  load or importance. `Σ m_i Δc_i` was rejected: it drops added/removed parts and the volume
  change, and produces the false impression of a list that is all-positive and sums to 100 %.
- **`hao`-free axis names.** The dominant axis is printed as `+X` / `−X` / `+Y` / `−Y` / `+Z` /
  `−Z` (U+2212). "Front/back/left/right" are modelling conventions the tool cannot know.

## 3. Traversal rule: leaves, never the container (measured, and deliberately not "container wins")

A STEP assembly imports as a tree: one root `App::Part` container whose `Shape` is a Compound of
all children, one `Part::Feature` per part, plus an `App::Origin` datum tree (axes/planes/points).

- **Adding every object double-counts**: the container's Shape is already the union of its
  children. Measured ratios 2.0 / 2.0 / 4.0.
- **Using the container alone throws away most of the product.** The container is a Compound, and
  `Part::Compound` has **no** `MatrixOfInertia` (pitfall F6). Measured on four real public
  assemblies (Go Kart, Colour-Science-XYZ, ekvc kart, Voron-0): "container wins" leaves **one**
  object per revision whose label is the assembly name — no per-part attribution, and **no inertia
  at all** on every one of them.
- **Therefore: take an object only when it has a `Shape` and none of its children can produce one;
  otherwise descend.** This yields 48 / 780 / 233 / 48 parts on the same four files, each with its
  own tensor, and totals that agree with the container-based figure to within 1e-3 (the leaf sum
  is "the material of the parts added up", and it matches decision **D-015**, where a container is
  defined as a duplicate of its children and excluded from comparison).
- The fallback matters: a container whose children are only datums (nothing can produce a shape)
  must still contribute **its own** Shape, otherwise the file silently becomes "0 objects".
- Objects of other types (`App::Line` / `App::Plane` / `App::Point` / sketches) carry no mass
  properties: they are neither counted nor allowed to make a parent look "covered".

## 4. Subprocess boundary, budget, and the hard deadline

Mass-property queries run in `perf_probe.py`, started by the pipeline as its own process, exactly
like the boolean worker — **"there is no solver" does not mean "the query is cheap"**. Measured
cost is 19–45 ms per object, while the same shape family has been measured running 840 s without
returning on a boolean. Only a separate process can be killed.

- `--perf-budget` is enforced **per revision** inside the probe; every object not reached is
  recorded as `unavailable` with reason `budget_exceeded` (never dropped silently).
- The parent's deadline is `2 × budget + 300 s`: the probe stops itself at the budget, so the
  parent only needs to cover the two STEP imports, which are outside the budget.
- The probe is also where the two revisions get their own temporary documents: importing both into
  one document would double `doc.Objects` and make old/new indistinguishable.

## 5. Field contracts

### `perf_snapshot.json`

`{"old": <snapshot>, "new": <snapshot>}`, each snapshot being:

```
side, n_objects, n_zero_volume, n_negative_volume,
total_volume_mm3, total_area_mm2, centroid_mm[3],
inertia_about_centroid_6[6] | null, principal_moments[3] | null, principal_axes[3][3] | null,
bbox_mm[3], objects[], unavailable[], elapsed_s
objects[]  = {label, base_name, volume_mm3, area_mm2, bbox_mm[3], centroid_mm[3],
              centroid_source, tensor_6[6] | null}
unavailable[] = {label, reason}
```

`reason` is a machine-read enum, never translated: `no_shape`, `no_centroid`, `non_finite`,
`budget_exceeded`, `negative_volume`. `centroid_source` records which fallback answered
(`CenterOfMass` → `CenterOfGravity` → `StaticMoments/Volume`) — `Part::Compound` raises
`AttributeError` on the first and the third.

`inertia_about_centroid_6` is `null` when **any** counted object has no tensor: a tensor summed
over a subset would look like a measurement while silently missing a part. Principal moments are
`null` when the tensor is missing or fails the physical validity check (§7).

### `perf_diff.json`

```
delta_volume_mm3, delta_volume_pct | null,
mass_kg_old | null, mass_kg_new | null, delta_mass_kg | null,
centroid_shift_mm, centroid_shift_components[3], dominant_axis | null,
delta_inertia_6[6] | null, delta_principal_moments[3] | null,
contributors[] = {label, base_name, d_mm[3]},
old{} / new{}  (the absolute quantities the report needs: total volume, area, centroid, inertia,
                principal moments, bbox, n_objects, n_not_counted, elapsed_s),
settings{rel_tol, budget, density_source, density_kg_per_mm3},
degraded_reason | null, unavailable[] = {side, label, reason}
```

`delta_volume_pct` is `null` when the old total volume is 0 — a near-zero baseline has no
meaningful percentage, and writing `0` or `∞` would both be wrong numbers.

`degraded_reason` is `null` in the normal case, otherwise `no_volume` (nothing to allocate over)
or `contributor_allocation_mismatch` (the allocation identity failed — reported, never hidden).
`unavailable[].reason` adds `no_inertia_tensor` and `no_principal_moments` to the snapshot enum.

`contributors` is keyed by object **label** (duplicate labels are summed), so a renamed part shows
up as two entries rather than being silently merged — the family-level `base_name` is carried
alongside for cross-referencing with the geometry layer, which pairs by name.

## 6. How the report uses it

The report reads `summary.performance_diff` only (never `perf_diff.json` / `perf_snapshot.json`
directly — that would create a second source of truth).

- A row is rendered **only when the quantity exists**: no inertia row when the tensor was
  unavailable, no dominant-axis row when the shift was below tolerance. A missing value must not
  be displayed as `0`.
- The honesty line *"Based on {n} closed solids only; {m} surface/shell objects were not counted."*
  goes into the "Not compared / not resolved" section, with `n` = counted solids **of the new
  revision** and `m` = `n_zero_volume + len(unavailable)` (shells, degenerate solids, non-finite
  objects, objects not reached within the budget). It is shown when `m > 0` or when nothing at all
  was counted.
- Disclaimers: "Material density is not confirmed…" only while `settings.density_source != "cli"`;
  the centre-of-gravity and stiffness sentences always; the allocation sentence only when
  contributors are listed.
- When the run reports **no differences** and a performance block exists, the section is prefixed
  with *"Geometry and BOM are unchanged; the performance figures below differ only within the
  configured tolerance."* — otherwise a reader would see "no differences" and a set of numbers that
  appear to contradict it.

## 7. Pitfalls hit (all measured; do not "fix" these back)

- **F2 — `MatrixOfInertia` is already in world coordinates, about the object's own centroid.**
  Nested `Placement` is already composed in. Multiplying by `R·I·Rᵀ` again applies the rotation
  twice. Verified by an analytic 200×20×20 beam rotated 45° about Z.
- **F3 — the tensor must be accumulated in two passes.** First the assembly centroid `C`, then the
  parallel-axis term per object with `d = c_i − C`. Incremental accumulation (a different reference
  point every round) was measured to shift `Jyy` by `M·50²`.
- **F5 — `PrincipalProperties.Moments` is returned in descending order**, which does not match the
  `A11/A22/A33` axis order (a mix-up measured a factor of 50). Principal moments must be computed
  as eigenvalues of the **aggregated** tensor; per-part principal moments must not be summed.
- **F6 — `Part::Compound` raises `AttributeError`** for `CenterOfMass`, `StaticMoments`,
  `MatrixOfInertia`, `Mass` and `PrincipalProperties`, while `CenterOfGravity`, `Volume` and `Area`
  work. Hence the three-level centroid fallback and the nullable tensor.
- **F7 — real files contain many non-solid objects**: one measured 193-object assembly was 4 solids
  + 189 zero-volume surface/shell objects (±1e-16), plus negative-volume degenerate bodies and
  objects whose centroid cannot be obtained at all. All of them are counted and reported; a
  negative volume is **never** `abs()`-ed, which would turn a broken body into a normal one.
- **F8 — the density trap** (see §1).
- **Tensor validity needs a relative slack.** A genuine inertia tensor satisfies
  `λ_i ≤ λ_j + λ_k`, but a thin rod sits exactly at the equality, so a strict comparison would
  reject valid parts. The check uses a relative slack and reports failure as "unavailable" instead
  of taking absolute values (which would dress a broken tensor up as a valid one).

## 8. Known limitations (deliberate)

- **Material/density changes are not covered** by the diff: the audience's STEP files usually carry
  no material information. `--density` describes one density for the whole assembly; a change of
  material between revisions is not detectable this way.
- A file that imports as **one object with no child objects** (a single compound holding several
  solids, e.g. a fused multi-body part) is reported as one part: per-part attribution and the
  inertia tensor are then unavailable. Splitting such a compound into its solids is not attempted —
  the solids carry no part names to attribute them to.
- Objects not reached within `--perf-budget` are reported as not counted, not estimated.
- Heuristic performance indicators (slenderness, stress-raising geometry) and solver-based
  comparison (T1/T2) are **out of scope**: the former needs a second moment of area and a
  buckling model; the latter requires the same physical load case and mesh-error control on both
  revisions, and is explicitly not to be shipped inside this repository.
