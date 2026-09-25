# Build DAS Meeting Assistant

For **installing and configuring the app for a user**, start with
[the agent installation runbook](../docs/agent-install.md). It prefers a published
Community installer and supports source execution when none is available. The build
toolchain below is only needed to produce your own packaged application or installer.

Builds accept one validated JSON deployment profile. Only explicitly allowed
public IDs, HTTPS endpoints, model names and selected defaults are accepted.
API keys, client secrets, tokens, arbitrary environment variables and unknown
profile fields cause validation to fail, even when their value is blank.
Endpoint URLs cannot contain credentials, query strings or fragments.

## Prerequisites

Windows, Python 3.12 with requirements-dev.txt, Node.js and Inno Setup 6 on PATH
(or installed in its default directory). Use -AppOnly when intentionally producing
an unpacked test application without Inno Setup.

The current notice snapshot uses Python 3.12.14 and the pinned dependencies.
Other versions require an intentional refresh/review of the third-party documents;
normal builds reject stale or missing documents rather than silently shipping them.

## Third-party material and build isolation (0.40.9)

Both installers include LICENSE, NOTICE, THIRD_PARTY_NOTICES.md and `third_party/`,
including corresponding source archives for LGPL/MPL libraries. Every build checks
document hashes and package versions and writes `component-inventory.json` with
the actual Python modules and hashes of native binaries. See
`third_party/README.md` for the update procedure.

PyInstaller's DLL search PATH is restricted to the selected Python and Windows.
Native binaries outside those roots fail the build, except for explicitly named
Microsoft VC runtime DLLs from System32. Windows debug helpers and UCRT/API-set
DLLs are resolved from Windows, not copied from other installed applications.
FFmpeg/PyAV/WebRTC dependencies and PyInstaller's own build modules are excluded.
The Speech hook ships the PCM core used by this application; the WebView hook
ships its x64 Windows components. pystray remains editable source outside PYZ.

Offline checks of an unpacked build (use its actual profile executable):

```powershell
$env:VOICE_TRANSCRIBER_DATA_ROOT = 'C:\local-test\das-cleanup'
.\dist\managed\MeetingTranscriberManaged\MeetingTranscriberManaged.exe --runtime-smoke-test
.\dist\managed\MeetingTranscriberManaged\MeetingTranscriberManaged.exe --ui-smoke-test
```

The runtime check constructs the Speech PCM/transcriber pipeline, resamples audio,
loads the icon/tray and imports the WebView2/.NET assemblies. It does not open audio
devices or start Azure recognition. Results are under
`<test-root>\MeetingTranscriber\Managed\`; failures write `runtime-smoke-error.txt`.
The HTTP check verifies the bundled frontend via the application's actual server.

## Validate inputs without a build

```powershell
powershell -ExecutionPolicy Bypass -File packaging\build.ps1 -ValidateOnly
powershell -ExecutionPolicy Bypass -File packaging\build.ps1 -Profile C:\private-release\managed.json -ValidateOnly
```

packaging/profiles/managed.example.json deliberately contains blank required values
and cannot be built until the provider supplies its public service configuration.
Organization-specific production configuration is maintained outside this repository.

## Build both distributions

```powershell
powershell -ExecutionPolicy Bypass -File packaging\build.ps1 -Profile packaging\profiles\community.json
powershell -ExecutionPolicy Bypass -File packaging\build.ps1 -Profile C:\private-release\managed.json
```

Outputs:

- dist/community/MeetingTranscriberCommunity/
- dist/managed/MeetingTranscriberManaged/
- packaging/Output/DAS-Meeting-Assistant-Community-Setup-<version>.exe
- packaging/Output/DAS-Meeting-Assistant-Setup-<version>.exe

Each variant has a distinct installer ID, directory, executable, shortcut, Windows
mutex and default local port. They can be installed side by side. Do not record
the same conversation with both variants during testing. Custom port changes must
remain distinct.

Existing installations and their old %LOCALAPPDATA%/MeetingTranscriber files are
left in place. There is no automatic credential or settings migration. Exit the
old client before testing Managed, which uses the old default port 8765.

## Product name and updates (0.40.8)

The DAS installer, Start Menu, desktop shortcut, tray and windows display
**DAS Meeting Assistant**. The separate Community build adds **Community**.
The former default Start Menu group is renamed; a customized group is retained.
Old shortcuts for the same distribution are replaced. The installer retains
previous desktop/autostart task selections.

Keep `AppId=DAS.VoiceTranscriber.<profile>`, executable names, installation
directories, mutex and `%LOCALAPPDATA%/MeetingTranscriber/<profile>` unchanged.
They are compatibility identifiers, not product labels. An existing 0.40.x
installation is updated in place without moving credentials, settings or meetings.
The earlier original app mentioned above remains a separate installation.

## Microsoft component consent (0.40.10)

Both installers show a required license page before installation, in German or
English according to the Windows UI language (`/LANG=german` or `/LANG=english`
can override it). Acceptance is never preselected or inherited on upgrade.
The complete Speech SDK and both VC runtime contracts are displayed offline;
the component-scoped agreement preserves Apache/LGPL/MPL and other OSS rights.

For an unattended deployment, an authorized administrator must first review
`third_party/microsoft-consent/microsoft-components.en.rtf` (or `.de.rtf`) and
explicitly pass the agreement revision:

```powershell
.\DAS-Meeting-Assistant-Setup-0-40-10.exe /VERYSILENT /SUPPRESSMSGBOXES /NORESTART /ACCEPTMICROSOFTTERMS=2026-09-22
```

`/SILENT` or `/VERYSILENT` alone aborts before stopping the app or changing files.
Missing, wrong, obsolete, conflicting or duplicate consent arguments are rejected.
The argument is only used for silent deployments; an interactive install still
requires the user's selection. Do not infer acceptance from an INF file, a prior
installation or suppressed messages. After successful installation,
`microsoft-terms-acceptance.txt` in the application directory records the revision,
text SHA-256, app version, language, method and local timestamp, without user IDs.
The receipt is not used to bypass future consent and is removed on uninstall.

After an intentional agreement change, review the texts and update the revision
in `packaging/prepare_microsoft_terms.py` when the accepted terms change, then run:

```powershell
.venv\Scripts\python.exe packaging\prepare_microsoft_terms.py --write
.venv\Scripts\python.exe packaging\third_party.py --refresh
```

Normal builds verify those generated files against the complete source texts and
the notice manifest. Do not edit the generated RTF/include files manually.
The VS developer license is our distribution basis, not an end-user requirement.
Original developer/runtime documents remain in `third_party/upstream/msvc/`.

To exercise the actual Pascal consent code without installing the app, changing
shortcuts/registry entries or stopping a running client:

```powershell
.\tests\test-installer-consent.ps1 -Iscc 'C:\Program Files (x86)\Inno Setup 6\ISCC.exe' -OutputDirectory 'C:\local-test\consent'
```

## Signing

Signing settings are build-only and explicitly supplied; there is no automatic
loading of a production signing profile. Use packaging/signing.env.template as
a local template, or configure the existing supported signing environment variables.

```powershell
powershell -ExecutionPolicy Bypass -File packaging\build.ps1 -Profile C:\private-release\managed.json -SigningConfig C:\private-release\signing.env -RequireSignature
```

-RequireSignature fails when no signer is configured or signature verification fails.
Builds without it may be unsigned development artifacts and are not customer releases.

## Build checks

1. Validate the profile before npm or packaging. -BundleEnv is no longer an accepted parameter.
2. npm ci uses the committed frontend lockfile and native command failures abort the build.
3. Stage a canonical deployment-profile.json; delete it in finally after PyInstaller.
4. PyInstaller validates the profile again, including when invoked directly.
5. Audit the unpacked application's embedded profile and reject forbidden state,
   .env files, private key containers and raw audio files.
6. Write build-manifest.json with source commit, dirty state, profile hash and
   executable hash after signing. Inno Setup includes that audited directory.
7. Require successful installer compilation; optionally require valid signatures.
   The installer SHA-256 is printed after creation.

The artifact audit is a specific build safeguard, not an exhaustive secret or
dependency audit. Before public release, run a repository/history secret scanner,
review dependencies and licenses, check the unpacked artifact, and test the
installer on a clean Windows machine. A dirty source manifest is a development build.

Builds in one checkout must run sequentially: profile staging and frontend output
are shared. Use separate clean checkouts for concurrent CI builds.
