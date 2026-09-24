# Sources and replacement of LGPL/MPL components

The original archives in `sources/` accompany the installer; their versions,
original download locations and hashes are in `sources.json`. They are supplied
unchanged. Keep these archives with any redistributed binary release.

## pystray 0.19.5 — LGPL-3.0-or-later

The packaged library is ordinary, editable Python source under
`_internal/pystray/`, outside the executable's Python archive. Close the app,
back up this directory and replace the affected `.py` files with compatible
modified files. No binary relinking or app signature is required. The supplied
upstream source archive includes the library, packaging scripts and license texts.
Its Windows backend is included; other OS backends are not used by this Windows app.

## soxr 1.1.0 — LGPL-2.1-or-later

The supplied sdist includes the Python binding, libsoxr, PFFFT and CMake build
files. To produce a replacement on Windows x64, use Python 3.12, MSVC C++ build
tools and the CMake/build requirements in the archive's `pyproject.toml`:

```powershell
python -m venv soxr-build
.\soxr-build\Scripts\python.exe -m pip install --upgrade pip
# Extract sources/soxr-1.1.0.tar.gz; edit the sources as needed.
.\soxr-build\Scripts\python.exe -m pip wheel --no-deps .\soxr-1.1.0 --wheel-dir .\wheels
```

Build isolation installs the requirements declared by the upstream project.
Open the resulting wheel as a ZIP and replace the matching files in
`_internal/soxr/`, including `soxr_ext*.pyd`, with the app closed. Preserve the
module name, x64 architecture and CPython-compatible ABI. The loader uses this
external extension, not a copy embedded in the executable. A compatible modified
version can therefore be used without rebuilding the DAS application.

The commands describe the upstream build; they have not been used to claim a
byte-identical rebuild of the upstream wheel. Changes to the library's public
API may require corresponding application changes.

## certifi and tqdm — MPL-covered source

The corresponding unmodified upstream source archives and license texts are
supplied. certifi's certificate bundle also remains an external file in the
installed application. Source changes to MPL-covered files retain their MPL
obligations. Merely including these libraries does not change the license of
the application's own independent source files.

## Complete application build

See `packaging/README.md` in the application source. It contains the pinned
dependency installation and Community/DAS build commands. Application source
publication is a separate release step; this document does not imply a release
has already been published.
