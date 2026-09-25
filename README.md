# DAS Meeting Assistant

Windows desktop assistant for Teams meetings: live transcription, summaries,
editable action items, Outlook invitation context and optional OneNote storage.

The client is developed by Data Assessment Solutions. Its source is licensed under
[Apache-2.0](LICENSE); bundled third-party components retain their own terms.
See [NOTICE](NOTICE), [source attribution](SOURCE.md) and [third-party notices](THIRD_PARTY_NOTICES.md).

## Install with your AI agent

Give an agent that can operate your Windows computer this repository link and say:

> Install https://github.com/data-assessment/DAS-Meeting-Assistant for me.

**Agents: start with [the installation runbook](docs/agent-install.md).**
The same entry point is recorded in [AGENTS.md](AGENTS.md). The agent should install
the Community edition, configure your own Microsoft/Azure resources and verify the
result. It can prepare the software while you complete account sign-in, MFA or an
administrator approval. A chat-only agent without access to your PC cannot perform
the local installation itself.

Use a Windows x64 desktop. Teams/Outlook/OneNote integration is intended for a
Microsoft 365 work or school account. Live Meeting Notes needs an Azure Speech
resource and an Azure-hosted chat-model deployment; it is not an offline model and
does not include DAS service access. Cloud usage is billed separately by your provider.
The agent will first look for existing resources and clarify costs before creating any.

The runbook also covers running from source when no Community installer is published.
Successful setup means a verified end-to-end test, not just a successful download.

## Distributions

- **Community:** use your own Microsoft/Azure configuration. Local state is isolated
  under `%LOCALAPPDATA%/MeetingTranscriber/Community`.
- **Organization-managed:** an administrator supplies a validated deployment profile.
  Local state is isolated under `%LOCALAPPDATA%/MeetingTranscriber/Managed`.

Both distributions use this codebase. Real organization profiles, operational
records, service credentials and internal release processes are maintained separately.
Checked-in profiles contain examples and generic defaults only. See
[deployment models](docs/deployment-models.md) and [build instructions](packaging/README.md).

## Development

Use Windows, Python 3.12.14 and Node.js:

```powershell
python -m venv .venv
.venv/Scripts/python.exe -m pip install -r requirements-dev.txt
Copy-Item .env.example .env
# Configure your own Microsoft application and Azure resources in .env / Settings.
Push-Location frontend
npm.cmd ci
npm.cmd run build
Pop-Location
.venv/Scripts/python.exe app.py
```

Source development without a deployment profile defaults to Community. Packaged
applications require a validated `deployment-profile.json`; they do not read an
`.env` file from the installation directory. Build profiles cannot contain keys or tokens.
For hot reload, the frontend proxy defaults to port 8766; managed instances default to 8765.

Run `.venv/Scripts/python.exe -m pytest -q` for isolated regression tests. See
[testing](docs/meeting-notes-test.md) for browser and packaged application checks.

## Data handling

Meeting Notes processes live audio and transcript text in RAM and sends them to the
configured Azure services for recognition and summarization. Derived meeting notes,
action items and selected people are persisted locally; OneNote can be selected as
the document destination. Every meeting creates a new OneNote page. Task corrections
can be synchronized to that page. Markdown is not an additional normal destination
for a successfully published OneNote meeting.

The separately selectable legacy recording flow can persist audio and transcripts.
Outlook invitation participants are not an attendance record. Optional delegated
`Chat.Read` access is requested explicitly; chat bodies are not evaluated or sent to
the text model. The client can operate independently of a platform backend.

## Release preparation

See [release checks](docs/open-source-release.md). Installer availability and signing
status are documented per release; building from source does not imply a signed binary.
An isolated [Speech prototype](prototypes/speech/README.md) is available for audio testing.
