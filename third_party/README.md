# Dependency documents

This directory contains original upstream documents, without replacing them with
summaries. `manifest.json` records their hashes and the installed package versions.

- `licenses/python/`: documents supplied with the pinned Python packages.
- `licenses/npm/`: original licenses of the frontend's production dependencies.
- `licenses/python/` also contains dependency notices shipped by NumPy and other packages.
- `licenses/python/LICENSE.txt`: license of the selected Python runtime.
- `licenses/pyinstaller/`: bootloader exception and runtime-hook terms.
- `upstream/`: supplemental upstream documents, including WebView2 SDK and native-library notices.
- `MICROSOFT-REDISTRIBUTION.md`: Speech redistribution conditions and the VC++ version/license mapping.
- `upstream/msvc/`: original runtime/developer DOCX terms, searchable text extracts,
  REDIST references and runtime-provenance.json. Developer terms document our
  distribution basis; they are not Visual Studio use terms imposed on app users.
- `sources/`: complete upstream source archives for pystray, soxr, certifi and tqdm.
- `sources.json`: source archive URLs, versions and SHA-256 hashes.
- `upstream-provenance.json`: supplementary document provenance.
- `python-runtime.json`: public Python 3.12.10 Windows download sources, hashes,
  validation method and native component review. Older supplemental notices remain
  a superset; they do not assert that every historical version is in the binary.
- `rust-manifest.json`: exact Cargo.lock sources and the complete set of 259
  locked crate versions used by cryptography, pydantic-core, jiter and watchfiles.
  This deliberately includes build/test and other-platform dependencies; it is
  a notice superset, not a claim that all crates are present in the Windows binary.

After an intentional dependency update, install the pinned Python and frontend
dependencies, review new native components and retrieve the matching source
archives/notices. Then run:

```powershell
.venv\Scripts\python.exe packaging\third_party.py --refresh
```

Normal builds only verify and copy the reviewed documents; they do not download
new license texts or silently change the dependency versions. A changed lockfile,
Python/PyInstaller version or missing/altered document stops the build.

These files document third-party terms. They do not grant rights beyond those
terms or certify that every proposed future distribution complies with them.
