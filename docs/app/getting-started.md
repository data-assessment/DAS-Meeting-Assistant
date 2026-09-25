# Getting Started

DAS Meeting Assistant runs as a Windows tray app. The recommended **Meeting Notes**
mode processes live meeting audio through Azure Speech, generates summaries and
editable tasks, and saves the resulting notes locally or to OneNote.

For a new Community installation, give your AI agent the repository link and ask it
to install the app. The README links to an agent installation runbook that covers
your own Azure resources, Microsoft sign-in and a verified first meeting. The source
is open source; it does not include cloud-service usage or DAS account access.

## Main window

Open Meeting Notes from the tray. In Community settings, select **Meeting-Notizen
statt Dateiaufzeichnung**, configure Speech and the text-model deployment, and choose
the microphone and the playback device used by Teams. Save the settings before
starting. Meeting Notes has its own Azure credential fields; configuring the general
batch-transcription settings alone does not configure this mode.

Use **Start** to begin a meeting with the participants' consent. Notes are updated
during the conversation; **Stop** ends capture and finishes the summary and tasks.
Closing the meeting window alone does not stop an active recording.

## Auto and Pause

The **Auto** / automatic-start control determines whether detected meetings start
automatically. Keep it off while setting up and testing the application.

When it shows **Auto**, the app starts recording as soon as Microsoft presence indicates an active Teams call or meeting.

Click it to switch to **Pause**. In Pause mode, the app can still show that a meeting was detected, but it will not start recording until you press **Start**.

Click **Pause** again to resume automatic recording. The choice is remembered across app restarts.

## Processing and storage

Meeting Notes processes raw audio and transcript text in RAM and sends them to your
configured Azure services. Derived notes and tasks are saved; this mode is not an
offline AI service. The app captures the selected sound devices and does not itself
activate a Teams recording notification.

The separate legacy recording mode can save WAV audio and transcript files and has
its own live-transcript and batch-processing controls. Turning off its live transcript
does not make Meeting Notes local-only. See **Recordings and Retry** for that mode.

## After a meeting

After a meeting, review the summary and tasks. Meeting notes are saved automatically to the selected destination: this PC or a new OneNote page. You can edit task details afterward or move locally saved notes to OneNote.

## Settings

Open Settings from the tray menu. In the managed app you can change meeting and summary preferences, configure OneNote, and choose local storage locations. Microsoft identity, cloud routing, API versions and available models are managed by the organization and are shown only as service/connection status. Standalone installations additionally expose their customer-owned Microsoft and Azure OpenAI infrastructure.
