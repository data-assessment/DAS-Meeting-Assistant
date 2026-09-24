# Microsoft component distribution

The application source is Apache-2.0. Microsoft Speech and Visual C++ binaries retain
their original licenses; the complete installer therefore includes components under
additional terms. This document records the shipped component mapping, not a distributor's
private license entitlement or legal advice.

## Speech SDK

The exact installed Speech SDK 1.51.2 documents are under
`licenses/python/azure-cognitiveservices-speech/` and indexed by `manifest.json`.
Read the original `speech/LICENSE.md`, `speech/REDIST.txt` and `speech/ThirdPartyNotices.md`.
The PCM core DLL and Python wheel are identified by the SDK's redistribution list.
Distribution is subject to its conditions, including application functionality,
protective recipient terms, notices and the applicable indemnification provisions.
Sample-source licenses do not replace the SDK's binary distribution terms.

## Visual C++ runtime

Each distributor must establish the distribution rights applicable to their own Visual
Studio license and Microsoft's distributable-code list. End-user runtime terms alone
are not proof of a developer's distribution entitlement. Keep entitlement evidence private.
Original agreements are collected in `upstream/msvc/`.

| Bundled runtime | File version | Recipient terms |
|---|---|---|
| MSVCP140.dll | 14.50.35719.0 | V14/2026 |
| MSVCP140_CODECVT_IDS.dll | 14.50.35719.0 | V14/2026 |
| VCRUNTIME140.dll | 14.44.35211.0 | 2015–2022 |
| VCRUNTIME140_1.dll | 14.44.35211.0 | 2015–2022 |
| NumPy's renamed msvcp140 DLL | 14.40.33810.0 | 2015–2022 |

Hashes and native provenance are in [runtime-provenance.json](upstream/msvc/runtime-provenance.json).
Pin official runtime sources for future builds; a changing operating-system installation
is not by itself a reproducible source. Python and NumPy licenses do not replace the
separate Microsoft conditions for these DLLs.

## Recipient agreement

The installer includes full component-scoped agreements and requires explicit acceptance.
Silent installation requires the documented versioned acceptance argument. The agreement
does not impose the Microsoft component restrictions on the application's Apache source.
See [packaging/README.md](../packaging/README.md) and `microsoft-consent/packet.json`.
Build checks verify the original terms, generated agreement and recorded hashes.
