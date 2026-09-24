# Troubleshooting

## Startup shows `{"detail":"Not Found"}`

Update the DAS Managed client to version 0.40.7 or later. Older versions could
open another application's local web server if the configured port was already
in use. The update selects a free port automatically and verifies that the UI
comes from this Transcriber process before opening the window. Users do not need
to change port settings or Microsoft permissions.

If the app reports that its interface is missing or incomplete, run the installer
again. If startup still fails, the diagnostic log is at
`%LOCALAPPDATA%\MeetingTranscriber\Managed\transcriber.log`.

## Microsoft sign-in required

If the app says sign-in is required, use **Sign in** in the main window. Some configuration changes require signing in again so the token contains the current Graph permissions.

## Meeting detected but recording does not start

Check the **Auto / Pause** button in the main window. If it shows **Pause**, automatic recording is paused. Press **Start** to record this meeting manually, or click **Pause** to switch back to **Auto** for future detected meetings.

## No OneNote notebooks are visible

Check that `ONENOTE_SITE_PATHS` uses the correct SharePoint host. Tenant team sites usually use `https://tenant.sharepoint.com`, while personal OneDrive sites often use `https://tenant-my.sharepoint.com`.

Also confirm that the user can open the SharePoint site and notebook in the browser.

## Audio or transcription fails

If transcription fails, the WAV file remains in the recordings folder. Use **Retry transcription** after fixing the configuration or speech-to-text provider issue.
