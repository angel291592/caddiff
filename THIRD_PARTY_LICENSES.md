# Third-Party Licenses

`caddiff` is distributed in two forms, and they are **not** covered by the same
license:

1. **The source code in this repository** is licensed under the **Apache License
   2.0** (see [LICENSE](LICENSE)).
2. **The published Docker image** additionally contains third-party software -
   FreeCAD, Open CASCADE Technology (OCCT), and the Python runtime and libraries
   listed below. Each of those components stays under **its own upstream
   license**. The Apache-2.0 license of this repository does not replace,
   relicense, or extend to them.

If you redistribute the image, or anything derived from it, you are
redistributing those third-party components as well and must comply with their
licenses too.

## Components

| Component | License | Distribution form |
|---|---|---|
| FreeCAD | **LGPL-2.1-or-later, but not LGPL-only.** The official distribution also contains GPL-2.0-or-later files, LGPL-3.0 components (QSint), and GPL-3.0-or-later with Bison exception. Some historical packaged builds also shipped CC-BY-NC template resources (TechDraw). | Docker image only, installed with `apt` at image build time. Never committed to this repository. |
| OpenCASCADE (OCCT) | LGPL-2.1 **with the Open CASCADE Exception 1.0** (`OCCT-exception-1.0`). That exception only covers inlining of header files into object code; it does **not** waive the LGPL distribution obligations. | Docker image only, installed with `apt` at image build time. Never committed to this repository. |
| python-pptx | MIT | pip dependency, inside the image. Only used when `--pptx` is requested. |
| Pillow | MIT-CMU | pip dependency, inside the image. |
| numpy | BSD-3-Clause | pip dependency, inside the image. |
| Python | PSF-2.0 | Base runtime of the image. |

### What each component is

- **FreeCAD** - the CAD application that reads the STEP/STP assemblies and hosts
  the geometry and rendering work; `caddiff` runs it as a separate process.
- **OpenCASCADE (OCCT)** - the geometric modeling kernel that FreeCAD is built
  on and that performs the actual shape operations; `caddiff` never links against
  it directly.
- **python-pptx** - Python library for reading and writing PowerPoint (`.pptx`)
  files, needed only for the optional `--pptx` report export.
- **Pillow** - Python imaging library, used for image input/output in the
  rendering and report pipeline.
- **numpy** - array library used for numeric work in the pipeline.
- **Python** - the interpreter that runs the CLI inside the image, and the
  interpreter bundled with FreeCAD that runs the geometry steps.

## Compliance notes

### 1. Two separate license sets

The repository code and the image contents are governed by different licenses,
and they must not be described with a single label. Everything you find in this
git repository is Apache-2.0 unless a file says otherwise. Everything that the
Dockerfile installs at build time keeps the license of its upstream project -
predominantly LGPL-2.1-or-later and GPL-2.0-or-later for FreeCAD, LGPL-2.1 with
the OCCT exception for Open CASCADE Technology, and permissive licenses (MIT,
MIT-CMU, BSD-3-Clause, PSF-2.0) for the Python components. Calling the whole
project "Apache-2.0" without that distinction would be inaccurate.

### 2. How to obtain the corresponding source

Docker image tags correspond one-to-one with source tags in this repository: the
image tagged `X.Y.Z` is built by the committed `Dockerfile` from the git tag
`X.Y.Z`, so the exact build recipe for any image is always retrievable at that
tag. Inside the image, the upstream license and copyright texts installed by
Debian are left in place under `/usr/share/doc/*/copyright`. The complete
corresponding source for the LGPL components is available from their upstream
distributions: FreeCAD at <https://github.com/FreeCAD/FreeCAD> (with releases at
<https://github.com/FreeCAD/FreeCAD/releases>) and Open CASCADE Technology at
<https://github.com/Open-Cascade-SAS/OCCT>. Debian's source packages for the
exact versions installed by the image are available from
<https://sources.debian.org/>. If you need the corresponding source for a
specific image tag and cannot locate it, open an issue and we will point you to
it.

### 3. Why this is not copyleft propagation

`caddiff` interacts with FreeCAD and OCCT only across a process boundary: it
spawns the FreeCAD interpreter as a subprocess and exchanges plain files with it
(STEP/STP inputs in, geometry dumps and rendered images out). It does not
compile a Python extension against FreeCAD or OCCT headers, does not `dlopen`
their shared libraries, and does not pass complex structures through shared
memory. Under the usual reading of the LGPL, that arm's-length use does not
create a derivative work of those components, so the Apache-2.0 code in this
repository is not pulled under the LGPL or GPL. This is a property of the
architecture, not an accident: changing the boundary (for example by adding a
compiled binding or loading OCCT directly) would change the licensing analysis,
which is why the contribution rules forbid it.

### 4. Attribution: OCCT and the FreeCAD trademark

This product includes Open CASCADE Technology (OCCT), which is distributed under
the GNU Lesser General Public License version 2.1 with the Open CASCADE
Exception 1.0. The exception requires this use to be stated prominently, which
is done here and in [NOTICE](NOTICE). FreeCAD and its logo are trademarks of the
FreeCAD Project Association (FPA). This project is not affiliated with, endorsed
by, or sponsored by the FPA: it uses the FreeCAD name only to describe the
software it depends on, it does not use the FreeCAD logo, and it does not
suggest any official endorsement by the FreeCAD project.

## Repository contents

No GPL or LGPL binaries are committed to this repository. The image's
third-party components are installed at build time from their distribution
packages; what is committed here is the build recipe (the Dockerfile and its
supporting scripts), not the binaries.
