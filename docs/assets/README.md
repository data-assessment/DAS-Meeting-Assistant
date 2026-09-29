# Repository presentation assets

- `meeting-notes.png`: the app's Meeting Notes review, showing synthetic meeting
  content and invented people.
- `onenote-destination.png`: the app's destination picker with synthetic notebooks,
  sections and an `example.org` account.
- `manual-start.png`: the Meeting Notes start screen with manual start while automatic
  Teams start is off, rendered with simulated local API responses.
- `social-preview.svg`: editable source for the repository's sharing image.
- `social-preview.png`: rendered 1280 × 640 sharing image for GitHub repository settings.

After making the repository public, upload `social-preview.png` under
Settings → General → Social preview. GitHub does not offer an initial social-preview
upload for a private repository. See
[GitHub's instructions](https://docs.github.com/en/repositories/managing-your-repositorys-settings-and-features/customizing-your-repository/customizing-your-repositorys-social-media-preview).

The application screenshots render the frontend from source version 0.40.15
(`manual-start.png` including the manual-start control) with simulated local API responses. They demonstrate the interface, not a
live Azure connection or a completed OneNote export. No microphone, cloud account,
real meeting content, tokens or keys were used.

When refreshing images, use the current built frontend and a local fixture server,
following the isolation principles in [the test guide](../meeting-notes-test.md).
Do not take screenshots from a user's normal meeting history or credential settings.
