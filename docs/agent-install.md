# Install DAS Meeting Assistant for the user

Audience: an AI agent with access to the user's Windows machine, a shell and,
where needed, a browser or authenticated cloud tools. The user may provide only
the repository URL and “install this”. Execute this runbook; do not simply repeat
it back as instructions. Explain progress and speak in the user's language.

This runbook describes the Community **Meeting Notes** path in source version
0.40.15. On a different revision, check the linked implementation before adapting
commands. Cloud model availability and tenant policies must be discovered live.

## Outcome and defaults

- A working Community installation on the user's Windows x64 desktop, with a
  durable launch shortcut and its version/commit recorded.
- Live audio through the user's Azure Speech resource; summaries through their
  Azure-hosted chat deployment. No DAS subscription, gateway or internal profile.
- Meeting Notes enabled; automatic meeting start **off during setup**. Local note
  storage first; calendar context and OneNote added according to the user's needs.
- Verified persistence after restart and a small, authorized end-to-end test.

The code is Apache-2.0; cloud services and bundled components have their own terms.
No Azure account, paid capacity, organizational permission or recording consent is
implied merely by possession of this source. Reuse the user's already-authorized
resources. Batch any missing decisions into one short question, then continue the
independent local work. Do not ask for approval again for an unchanged approved plan.

## 0. Prove that you can operate the target PC

Before offering to install anything, establish where your shell actually runs and
which tools can write files and execute commands on the user's Windows desktop.
For a candidate local PowerShell tool, run this harmless probe:

```powershell
[Environment]::OSVersion.Platform
$PSVersionTable.PSVersion.ToString()
(Get-Location).Path
```

Require `Win32NT` **and** evidence that this is the user's target PC, rather than an
unrelated remote Windows host. A Linux sandbox does not become a Windows shell because
the user has a terminal open. Screen viewing or clicking also does not prove that your
tool can enter commands. If the tool cannot type/paste into the terminal, do not offer
that route as a working alternative or repeatedly ask the user to open more terminals.

If no usable local execution tool exists, explain the limitation immediately and offer:

- Continue with an agent that has a local Windows shell, using this same repo URL.
- A **guided manual handoff**, if the user wants it: present small, complete PowerShell
  blocks inline in the conversation, use the source steps below, and inspect the result
  of each stage before proceeding. Keep the agent responsible for interpreting errors.

Do not make a generated chat attachment the only route to installation. A file created
in your sandbox is not necessarily downloadable or present on the target PC. This repo
does not currently ship a script named `Install-DAS-Meeting-Assistant.ps1`; do not imply
that such a generated file is an official installer. Do not invent a script link, assume
that a download succeeded, or report installation after merely writing/parsing a script.

For a manual handoff, distinguish the four milestones explicitly: **commands prepared**,
**software installed**, **cloud/Microsoft configuration complete**, **function test passed**.
Stop at the actual milestone; a file attachment or source download establishes neither
installation nor a working Azure connection.

## 1. Discover before changing anything

1. Confirm you are operating on the target Windows desktop, not a remote Linux
   container or WSL. macOS/Linux and native ARM builds are not covered by this runbook.
2. Inspect running Community/Managed processes, existing shortcuts and installation
   paths. Do not close a client during an active meeting. Never run two captures of
   the same meeting. Preserve existing user settings, keys and notes on an upgrade.
3. Find the repository's README, this document and its releases. While the repository
   is private, authenticate with the user's authorized GitHub account; a 404 is not
   proof that the project does not exist. Do not make the repository public to install it.
4. Inspect installed Git, Python, Node.js, WebView2 Runtime and VC++ x64 runtime.
   Installer users do not need Python, Node.js or a build toolchain.
5. Discover the current Microsoft tenant, Azure subscription and usable resources
   with authorized tools. Do not print tokens or resource keys. Read
   [the Azure setup companion](agent-azure-setup.md) before provisioning anything.

Ask only what remains unknown: reuse or create resources, the intended account and
subscription, allowed region/cost, language, and whether OneNote is wanted. Default
to local notes and manual start while optional integrations are undecided. If the
user has no Azure access, finish the local installation and clearly identify who
must supply access; do not claim transcription is ready.

Keep a private, resumable setup record **outside the checkout**, for example under
`%LOCALAPPDATA%/DAS-Meeting-Assistant-Setup/`. Record the revision, installation route,
paths, resource IDs (not keys), choices, completed checks and remaining actions.
Do not upload this record or copy an existing installation's token cache.

## 2. Choose the installation route

### A. Official Community installer, when available

For the current external installation test, use the published
[Community 0.40.15 preview](https://github.com/data-assessment/DAS-Meeting-Assistant/releases/tag/v0.40.15-community-preview.1).
It is intentionally marked **prerelease** and unsigned. Do not overlook it by querying
only GitHub's latest stable release. Read its validation limits and installation notes;
the installer, `SHA256SUMS.txt` and `build-manifest.json` are attached to that release.

Inspect [GitHub Releases](https://github.com/data-assessment/DAS-Meeting-Assistant/releases).
Select a maintainer-published release with a Community asset named
`DAS-Meeting-Assistant-Community-Setup-<version-with-hyphens>.exe`. Match it to the
release version and published SHA-256. Inspect Authenticode status and report the
actual result; a hash is an integrity check, not proof of publisher identity.

Do not substitute `DAS-Meeting-Assistant-Setup-...exe`: that is the Managed edition.
Do not use old installers from unrelated shared folders. Do not invent a download
URL, checksum, signature or available release. If there is no suitable asset, use B.

Install for the current user. The installer creates shortcuts; locate the installed
`MeetingTranscriberCommunity.exe` from the shortcut/uninstall entry rather than
assuming Program Files. Review the component agreement using the workflow supported
by your agent. Do not infer agreement from “install this”, bypass the license page,
or invent an `/ACCEPTMICROSOFTTERMS` revision. If unattended installation is authorized,
use the exact current revision documented in [packaging/README.md](../packaging/README.md).
Leave application launch and Windows autostart disabled until step 4 is prepared.

### B. Source installation, when no Community release is available

This is a usable local installation with a shortcut, not a claim that you produced
a distributable installer. Use a user-writable, non-synced local folder such as
`%LOCALAPPDATA%/Programs/DAS-Meeting-Assistant-Source`. Inspect it before use; never
overwrite an unrelated checkout or discard uncommitted work.

Required: Git, x64 CPython **3.12**, Node.js **22 LTS** with npm, Microsoft Edge
WebView2 Evergreen Runtime and the Microsoft VC++ x64 runtime required by Speech.
Use official vendor distributions or their verified Windows package-manager entries;
inspect what is already installed first. Use maintained 3.12 patch releases for
source execution. Packaging has a stricter runtime/notice snapshot (currently
3.12.14); do not alter license hashes to force a different packaging environment through.

Run commands in PowerShell, checking `$LASTEXITCODE` after **each** native command;
stop and resolve failures instead of continuing with a partial installation:

```powershell
# Use this target only after checking that it does not already contain user work.
$ErrorActionPreference = 'Stop'
$checkout = Join-Path $env:LOCALAPPDATA 'Programs\DAS-Meeting-Assistant-Source'
if (Test-Path -LiteralPath $checkout) {
    throw 'The target already exists. Inspect it before choosing an install or update path.'
}
git clone https://github.com/data-assessment/DAS-Meeting-Assistant.git $checkout
if ($LASTEXITCODE -ne 0) { throw 'Clone failed. Check GitHub access before continuing.' }
Set-Location -LiteralPath $checkout
git rev-parse HEAD
if ($LASTEXITCODE -ne 0) { throw 'Could not identify the source revision.' }
py -3.12 -c "import platform, struct; print(platform.python_version(), struct.calcsize('P') * 8)"
if ($LASTEXITCODE -ne 0) { throw 'Python 3.12 is unavailable. Resolve prerequisites first.' }
py -3.12 -m venv .venv
if ($LASTEXITCODE -ne 0) { throw 'Python environment creation failed.' }
& .\.venv\Scripts\python.exe -m pip install -r requirements.txt
if ($LASTEXITCODE -ne 0) { throw 'Python dependency installation failed.' }
Push-Location frontend
try {
    npm.cmd ci
    if ($LASTEXITCODE -ne 0) { throw 'Frontend dependency installation failed' }
    npm.cmd run build
    if ($LASTEXITCODE -ne 0) { throw 'Frontend build failed' }
} finally { Pop-Location }
```

Prefer the commit/tag of the selected official release; if installing `main`, record
the exact commit and call it a source installation. Do not upgrade dependencies,
regenerate lockfiles or change app code just to complete setup. If wheels are missing,
check Python version/architecture first. Install no tools into another application's
Python environment. Do not copy `.env.example` wholesale: it enables legacy features
that this runbook deliberately does not need.

Check that `frontend/dist/index.html` exists. The application serves its own built
frontend; `npm run dev` is not a requirement for normal use. On a fresh source install,
there should be no local `deployment-profile.json` or root `.env` routing the app to
a managed service. Do not remove such files from an existing installation without
first understanding their purpose. Step 4 supplies a minimal per-user configuration.

Building an EXE/installer yourself is a separate optional route documented in
[packaging/README.md](../packaging/README.md). It needs the pinned build dependencies,
native runtime inputs, original license documents and (for an installer) Inno Setup.
Do not make that toolchain a prerequisite for simply running from source.

### Recovering a missing script or failed attachment

If PowerShell says the argument to `-File` does not exist, the named script did not
run. Check the prompt's working directory. For example,
`PS C:\Windows\System32>` with `-File .\Install-DAS-Meeting-Assistant.ps1` looks for
that file in System32, not in Downloads. Repeating the command or changing execution
policy will not create the missing file.

1. Establish whether the file was actually downloaded. An error such as “This file
   type cannot be opened” in the chat UI is not proof that the file exists on Windows.
2. If the user has the file, use its actual full path from Explorer (Copy as path).
   Check `Test-Path -LiteralPath` and inspect the script before running it. Quoted
   absolute paths work independently of the terminal's current directory. Never move
   the script into System32 or request elevation just to resolve a relative path.
3. If the file is unavailable, switch to the inline source-installation handoff above
   or to a local-shell agent. Do not keep issuing commands for the absent attachment.
4. For a private repo, browser access and Git authentication are separate. If cloning
   fails, resolve the user's GitHub sign-in for Git. A browser-downloaded repository ZIP
   is another source route: extract the complete archive to a durable user-owned folder,
   verify `app.py`, `requirements.txt` and `frontend/package-lock.json`, then begin with
   the Python/build commands from that actual source root. Do not download just a script
   and claim that the rest of the source is present. Record ZIP provenance separately;
   `git rev-parse` does not work in an extracted archive.

## 3. Prepare Microsoft/Azure access

Follow [agent-azure-setup.md](agent-azure-setup.md). Return with these facts resolved:

| Value | Use |
| --- | --- |
| Tenant ID and desktop app client ID | Microsoft sign-in, presence, optional calendar/OneNote |
| Speech resource region and key | Live mixed audio and speaker diarization |
| Azure text-model resource endpoint, deployment name and key | Meeting summaries and tasks |
| Approved optional permissions | Calendar, OneNote, optionally chat-person suggestions |

Keys must reach the local application's credential interface without appearing in
chat, screenshots, command history, shell arguments, files in the checkout or logs.
Use an authorized credential provider/secret-aware local process, or let the user
enter keys in the app's masked fields. Do not ask the user to paste keys into chat.
The agent should automate resource discovery and configuration with its available
tools; browser sign-in, MFA, consent or missing administrator rights may need a handoff.

## 4. Prepare safe first-run state

The normal Community data directory is
`%LOCALAPPDATA%/MeetingTranscriber/Community`. Source and packaged Community use the
same directory. Managed is separate. Do not set `LOCALAPPDATA` to simulate another
account. For disposable offline checks only, the app supports an absolute
`VOICE_TRANSCRIBER_DATA_ROOT`; remove that override before normal launch.

With the Community app stopped, on a **fresh** installation create the following
minimal files using UTF-8 without BOM. Resolve the two placeholders from step 3.
For an existing installation, back up and merge only the intended fields; never
replace its entire `.env` or `settings.json` with these examples.

`%LOCALAPPDATA%/MeetingTranscriber/Community/.env` (non-secret settings):

```dotenv
GRAPH_TENANT_ID=<user-tenant-guid>
GRAPH_CLIENT_ID=<user-desktop-app-client-guid>
GRAPH_AUTH_MODE=interactive
AI_MODE=local
FETCH_ATTENDEES=false
USE_TEAMS_TRANSCRIPT=false
ONENOTE_ENABLED=false
RECORD_AUDIO=false
HOST=127.0.0.1
PORT=8766
```

`AI_MODE=local` means direct access to the user's cloud resources, **not offline AI**.
`RECORD_AUDIO=false` disables the legacy WAV recorder; it does not disable Meeting
Notes' live audio. `FETCH_ATTENDEES` and `USE_TEAMS_TRANSCRIPT` control legacy Graph
features, not the separate Meeting Notes calendar-context feature.

`%LOCALAPPDATA%/MeetingTranscriber/Community/settings.json`:

```json
{
  "auto_start": false,
  "live_on": false,
  "meeting_notes_enabled": true,
  "meeting_notes_options": { "language": "de-DE" }
}
```

Choose the requested recognition language (for example `en-US` instead). The current
summary prompt produces German notes; do not promise translated summaries merely
because the recognition language changes. Blank Azure options are completed next.

If Graph access is not yet available, omit the two ID settings and keep automatic
start off. Manual Meeting Notes can still be configured and tested using Azure keys;
Graph-dependent features will remain unverified and may show a connection warning.

A packaged app does not load `.env` from its installation directory. General Settings
persists editable runtime configuration to the per-user `.env`. The general
`AOAI_API_KEY`/`AOAI_ENDPOINT` fields are for the other processing paths; they do **not**
populate Meeting Notes' `speechKey`/`chatKey` options. Configure those in step 5.

## 5. Launch and configure Meeting Notes

Launch the installed Community shortcut. For source execution, first launch
`.venv/Scripts/python.exe app.py` from the checkout to diagnose startup, then create
a current-user Start Menu shortcut targeting the absolute `.venv/Scripts/pythonw.exe`,
with the quoted absolute `app.py` as its argument, the checkout as working directory
and `favicon.ico` as icon. Keep the checkout and venv in place. Use hidden background
launches for helpers; do not enable Windows login autostart by default.

Open the Meeting Notes window from the tray. Its Community settings must expose
**Meeting-Notizen statt Dateiaufzeichnung** and the Azure fields below. A managed
“DAS sign-in” setup instead means the wrong distribution/profile is running.

| UI label | Configuration field | Required value |
| --- | --- | --- |
| Meeting-Notizen statt Dateiaufzeichnung | `enabled` | `true` |
| Speech-Region | `region` | Actual Speech region identifier, e.g. `westeurope` |
| Sprache | `language` | Supported locale, e.g. `de-DE` |
| Speech-Schlüssel | `speechKey` | Key for that Speech resource |
| Textmodell-Endpunkt | `endpoint` | HTTPS resource root, e.g. `https://<resource>.openai.azure.com` |
| Deployment | `model` | The actual deployed chat-model **deployment name** |
| Textmodell-Schlüssel | `chatKey` | Key for that text-model resource |
| Mikrofon | `mic` | Exact device name selected from the enumerated list |
| Teams-Wiedergabe / Headset | `loopback` | Loopback of the output device actually used by Teams |

Save. Do not start capture yet. Keys are encrypted using Windows current-user DPAPI
in `azure-notes.dpapi` in the Community data directory. Other options are saved in
`settings.json`. Do not edit/decrypt the DPAPI file or move it to another account.
Changing region/endpoint requires a matching key; leaving a key field blank keeps
its current value when the associated resource remains unchanged.

### Optional local API route for agents

Use UI automation if available. If using the app's local HTTP API, first identify
the listening port of **this Community process**. The preferred port is 8766, but the
app can choose another if it is occupied. The current URL appears as `local UI:` in
`transcriber.log`. Verify process ownership and `X-Voice-Transcriber-Instance` on
the local response; the header alone is not authentication. Never send keys to a
guessed port, a LAN host or a proxy. Keep the server bound to loopback.

Useful routes (see [notes_api.py](../engine/notes_api.py)):

- `GET /api/notes/devices`: enumerate names without starting capture.
- `GET /api/notes`: inspect `enabled`, `active`, `autoStart` and `options`. Inspect
  only needed fields; its `reviews` can contain existing private meeting content.
- `POST /api/auto-start/off`: disable automatic meeting start.
- `POST /api/notes/configure`: JSON with `enabled: true` plus the eight string fields
  in the table (except `enabled` itself). `mic` and `loopback` may be empty for Windows defaults.
- `GET /api/graph/status`: inspect sign-in state without initiating login.
- `POST /api/sign-in`: explicitly initiate the user's Graph sign-in.

For configuration, use `Content-Type: application/json`, no foreign `Origin`, and
a local request with proxies disabled. Build the body **in memory**, suppress request
body/header logging and inspect `ok` in the JSON response; HTTP 200 alone can contain
`ok: false`. Do not use `curl` arguments containing actual keys. A shell-only agent
can capture `az cognitiveservices account keys list` inside its local process and
pass the result directly to this request without emitting it to tool output.

Verify these non-secret values from `/api/notes`:
`enabled=true`, `options.managed=false`, `active=false`, `autoStart=false`,
`options.hasSpeechKey=true`, `options.hasChatKey=true`, both `*KeySaved=true`, and
an empty `options.credentialError`. Successful saving does not yet prove cloud access.

## 6. Sign in and enable the requested integrations

Manual capture also works for Zoom and other audio played through the selected PC
output device. Select the device used by that application, start capture manually
and stop it when finished. This path does not require Teams presence detection;
automatic call/meeting start is currently a Teams-only feature. Configure Microsoft
sign-in below for Teams automation or the requested calendar/OneNote integrations.

1. Use **Mit Microsoft anmelden** / the Graph sign-in control. Have the user complete
   account selection and MFA in Microsoft's browser. Do not create a client secret,
   copy browser tokens, or weaken Conditional Access. Verify configured/connected
   status with the intended account.
2. For Outlook context, use **Kalender verbinden** in the Meeting Notes person/calendar
   controls when offered. It requests `Calendars.Read` independently of the legacy
   attendance switches. Verify with an existing, user-authorized appointment; invited
   people are not a confirmed attendance list.
3. For OneNote, explicitly choose the integration and destination with the user.
   Enable `ONENOTE_ENABLED=true` in general Settings if needed for the legacy UI;
   Meeting Notes has its own OneNote connection using `User.Read` + `Notes.ReadWrite.All`.
   Connect from the Meeting Notes destination picker and choose the actual notebook
   and section. Only request `Sites.Read.All` if configured SharePoint-site discovery
   is needed. Never select a customer notebook merely because a name looks plausible.
4. Leave Teams chat-person suggestions off unless requested; the optional connection
   adds `Chat.Read`. If tenant policy requires an admin, report the exact app ID and
   requested delegated scopes rather than requesting broad application permissions.

Do not create meetings, send invitations or send messages to test the installation.
Any OneNote test creates a real page: agree on a test destination before saving.

## 7. Verify before declaring completion

Use separate checks with explicit outcomes; do not turn a successful build into a
claim that microphone, Azure or OneNote worked.

1. **Local startup:** tray/window works, Community selected, only loopback listening,
   auto-start off, correct microphone and Teams output enumerated. Check that the
   Windows desktop-app microphone permission allows this app.
2. **Restart:** close normally, reopen the same launch shortcut, confirm the non-secret
   readiness flags, devices and preferences persist. Do not print keys to verify them.
3. **Cloud and audio:** agree on a short test with invented content and consent from
   everyone audible. Explain that live audio goes to the user's Azure Speech resource
   and transcript text goes automatically to the configured text model while notes
   are generated. Start manually in Meeting Notes, speak on both local and remote
   sides, then Stop. Require final notes containing expected facts and a promised task.
   Empty audio, a startup check or “configuration saved” is not an end-to-end pass.
4. **Storage:** confirm the synthetic notes are locally saved and editable. If OneNote
   was requested, save to the agreed section, verify one page, and test one task edit.
   Do not repeatedly create pages when transfer status is uncertain.
5. **Normal use:** confirm the user's preference for automatic Teams meeting start;
   otherwise leave it off. Windows login autostart is a separate choice.

Meeting Notes keeps raw audio/transcript processing in RAM; derived notes, selected
people, tasks and internal state persist locally. OneNote sends derived notes to
Microsoft 365. The separate legacy recording mode can write WAVs/transcripts. Capture
uses the sound devices; it does not itself turn on Teams' recording notification.
Explain these distinctions without claiming the whole application is offline or
that the agent has established legal permission to record every future meeting.

If a required test cannot run, report **installed/configured; test pending**, the
specific missing condition and the next action. Keep independent completed work.

## 8. Finish, resume and update

Tell the user the installed version/commit, how to launch, where notes go, whether
auto-start is on, which checks passed, and any remaining user/admin action. Keep the
private setup record free of keys, tokens and meeting content. Do not uninstall or
replace the Managed edition as cleanup. Do not automatically delete cloud resources
or saved notes, even if they were created during setup.

For updates, stop capture first and preserve the Community data directory. Installer
updates retain the existing installation identity. Source updates require a clean
checkout, reviewing the target revision and dependency changes, reinstalling the
lockfiles if changed, rebuilding the frontend and repeating startup/readiness checks.
Never `reset --hard` over user changes. Cloud resources normally remain reusable.

## Troubleshooting decisions

| Symptom | Check and next action |
| --- | --- |
| Agent only has a Linux sandbox or cannot enter commands in Windows | Follow step 0: use a local-shell agent or guided inline commands; do not claim a local install. |
| `-File` says the script does not exist / attachment cannot be opened | Follow the missing-script recovery above; verify download and absolute path before execution. |
| GitHub 404 / no assets | Check access and actual release list; use source route if no Community release exists. |
| Wrong setup / asks for DAS access | Check executable/profile and `options.managed`; install Community, preserve Managed state. |
| Blank window / native DLL error | Check built frontend, Python x64/3.12, WebView2 and official VC++ runtime; do not copy DLLs from other apps. |
| Port already used | Determine this process's selected port; never kill an unrelated listener or send credentials to it. |
| Graph login fails / AADSTS error | Check tenant/client IDs, public desktop `http://localhost` redirect and tenant policy; capture the error code without tokens. |
| Graph asks for Teams transcript/attendance permissions | Verify `FETCH_ATTENDEES=false` and `USE_TEAMS_TRANSCRIPT=false`, restart; these are not required for live Meeting Notes. |
| Keys saved but Speech fails | Match key to Speech resource and region; verify resource access/network and Speech SDK support. |
| Summary 400/404 | Check root endpoint, deployment **name**, v1 chat/JSON support and request parameters; not a Foundry project URL or an API path. |
| Azure 401/403/429 | Check matching key/local-auth policy, network path, model access/quota; do not disable organizational controls or buy capacity without authorization. |
| Only own voice appears | Match loopback to Teams' actual output, not merely the Windows default; use headphones and retest. |
| No calendar/OneNote | Complete the feature's own delegated consent and verify access to the selected account/notebook; don't infer it from presence login. |
| Keys disappear after restart | Confirm same Windows user, same Community data directory, matching region/endpoint, and empty `credentialError`; never copy another user's DPAPI file. |

## Implementation and vendor references

Repository contracts: [paths](../paths.py), [configuration](../config.py),
[Meeting Notes options/requests](../engine/meeting_notes.py),
[DPAPI storage](../engine/notes_credentials.py), [Graph authentication](../engine/graph_auth.py),
[Community UI](../frontend/src/MeetingNotes.tsx), [test procedures](meeting-notes-test.md).

Current Microsoft setup references are linked in [agent-azure-setup.md](agent-azure-setup.md).
For Windows prerequisites use [WebView2 distribution](https://learn.microsoft.com/en-us/microsoft-edge/webview2/concepts/distribution)
and [VC++ redistributable guidance](https://learn.microsoft.com/en-us/cpp/windows/latest-supported-vc-redist).
Use these sources for current installers; do not hardcode third-party download mirrors.
