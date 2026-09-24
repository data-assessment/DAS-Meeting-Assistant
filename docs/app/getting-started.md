# Getting Started

DAS Meeting Assistant runs as a tray app. It watches Microsoft presence, records Teams meetings when recording is enabled, and writes transcripts after the meeting ends.

## Main window

The main window shows the current recording state, the meeting title, and any background processing jobs.

Use **Start** to begin a manual recording. Use **Stop** to finish the current recording and queue transcript generation.

## Auto and Pause

The **Auto** button controls whether detected meetings start recording automatically.

When it shows **Auto**, the app starts recording as soon as Microsoft presence indicates an active Teams call or meeting.

Click it to switch to **Pause**. In Pause mode, the app can still show that a meeting was detected, but it will not start recording until you press **Start**.

Click **Pause** again to resume automatic recording. The choice is remembered across app restarts.

## Live transcript

The live transcript can be opened while a meeting is active. It can be copied, searched, and optionally translated.

If live transcript is off, the saved transcript can still be created after recording from the local speech-to-text engine.

## After a meeting

After a meeting, review the summary and tasks. Meeting notes are saved automatically to the selected destination: this PC or a new OneNote page. You can edit task details afterward or move locally saved notes to OneNote.

## Settings

Open Settings from the tray menu. In the managed app you can change meeting and summary preferences, configure OneNote, and choose local storage locations. Microsoft identity, cloud routing, API versions and available models are managed by the organization and are shown only as service/connection status. Standalone installations additionally expose their customer-owned Microsoft and Azure OpenAI infrastructure.
