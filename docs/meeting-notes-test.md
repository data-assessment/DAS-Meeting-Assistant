# Meeting Notes validation

Tests use synthetic people, organizations and API responses. They must not read or
write the developer's normal meeting data, call real cloud services or create OneNote pages.

## Regression suite

```powershell
.venv/Scripts/python.exe -m pytest -q
```

The suite covers invitation matching, meeting titles, full-name assignment, live and
finished task editing, destination selection, local/OneNote persistence, sync conflicts,
authentication isolation, token renewal and managed-profile validation.

## Browser checks

Build the frontend with `npm ci` and `npm run build` in `frontend/`. Browser regression
scripts are under `frontend/tests/` and require Playwright supplied by the test runner.
They use a local static server and simulated API responses. They do not establish a real
Microsoft session or prove that a tenant has granted the required permissions.

## Packaged application

Set an absolute, fresh `VOICE_TRANSCRIBER_DATA_ROOT` and run the built executable with
`--runtime-smoke-test`, `--notes-smoke-test` and `--ui-smoke-test` separately. These checks
cover native Speech/audio/WebView imports, local note persistence and the application's
own HTTP UI. Do not change `LOCALAPPDATA` or copy an existing token cache for the tests.

## Manual acceptance with authorized resources

1. Install on a clean Windows account and complete Microsoft sign-in. Settings remain
   optional after successful sign-in and Finish clearly closes onboarding.
2. Join an Outlook meeting. Check the foreground window, invitation title and people list.
   Invitation participants are not a confirmed attendance list.
3. Change the destination during the meeting; verify customer-domain preferences on a
   subsequent meeting. Without a saved preference, local storage is the fallback.
4. Check summary expansion, task owner selection and task corrections after OneNote save.
5. Check a permitted group notebook. Saving creates one page; task updates keep that page
   and existing checkbox state. Concurrent same-paragraph edits remain a sync limitation.
6. Check an interrupted connection, reauthentication, restart and pending synchronization.

A synthetic smoke test cannot replace these tenant-specific end-to-end checks. Keep their
evidence, real resource IDs, account names and operational logs outside the public repository.
