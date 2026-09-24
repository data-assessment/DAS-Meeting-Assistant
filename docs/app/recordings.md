# Recordings and Retry

The app records WAV files while a meeting is active. These files are used as input for local speech-to-text transcription.

## Cleanup

After a successful transcription, the source WAV file is deleted automatically. If transcription fails, the WAV file is kept so it can be retried.

## Retry transcription

The **Retry transcription** action appears only when the recordings folder contains saved WAV files.

When you start a retry, the retry dialog closes and the queued transcription job appears in the main window.

## Transcript folder

When saved transcripts exist, use **Transcripts folder** in the main dialog to open their local folder in File Explorer.
