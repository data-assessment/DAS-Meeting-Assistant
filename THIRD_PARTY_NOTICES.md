# Third-party notices

DAS Meeting Assistant's own source is licensed under [Apache 2.0](LICENSE).
Its dependencies are separately licensed. In particular, the Microsoft Azure
Speech SDK is distributed under Microsoft's SDK terms, not under Apache 2.0.

Original license, copyright and notice documents are supplied in
[`third_party/licenses`](third_party/licenses) and
[`third_party/upstream`](third_party/upstream). The machine-readable
[`manifest.json`](third_party/manifest.json) records package versions, document
paths and SHA-256 hashes. It covers the locked runtime dependencies; an entry
does not mean every optional feature of that package is included in the binary.
The build's `component-inventory.json` records the actual Python modules and
native files in that distribution. Build tools are not application dependencies.

| Component | Included material / terms |
|---|---|
| Python | Runtime and standard-library notices from the selected Python distribution |
| Azure Speech SDK 1.51.2 | Original Microsoft LICENSE, REDIST and ThirdPartyNotices; PCM core only |
| WebView2 SDK 1.0.3856.49 | BSD-3-Clause LICENSE and NOTICE from Microsoft's exact NuGet package |
| pywebview / pythonnet / clr_loader | Their original package licenses |
| PyAudioWPatch | Apache-2.0 fork plus original PyAudio and PortAudio notices |
| pystray 0.19.5 | LGPL-3.0-or-later; source and GPL/LGPL license texts supplied |
| soxr 1.1.0 / libsoxr | LGPL-2.1-or-later and PFFFT notices; complete upstream source archive supplied |
| certifi / tqdm | Original MPL/MIT notices and corresponding upstream source archives supplied |
| NumPy / Pillow / cryptography | Package licenses, including collected notices for native dependencies |
| React and its runtime dependencies | Complete MIT licenses from the frontend lockfile's production packages |
| PyInstaller | Unmodified bootloader/loader under its distribution exception; runtime hooks under their stated terms |
| Microsoft Visual C++ runtime | 14.50 uses V14/2026 terms; Python's 14.42 and NumPy's 14.40 use 2015–2022 terms. Separate Visual Studio distribution grant; [review and exact mapping](third_party/MICROSOFT-REDISTRIBUTION.md) |

The Edge WebView2 browser runtime and Windows/.NET Framework are system
prerequisites. They are not installed or relicensed by this source repository.

The LGPL libraries remain separately replaceable. See
[`third_party/REBUILD.md`](third_party/REBUILD.md) for their sources and replacement
instructions. No additional restriction is imposed on modification or reverse
engineering needed to debug changes to these libraries. Existing third-party
copyright and attribution notices must be preserved under their applicable terms.

Release preparation and any outstanding distribution checks are recorded in
[`docs/open-source-release.md`](docs/open-source-release.md).
