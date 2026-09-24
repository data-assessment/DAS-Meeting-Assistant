# OneNote Pages

The OneNote dialog saves a transcript and summary into a selected notebook section.

## Notebook discovery

The app discovers notebooks from your OneNote account and from configured SharePoint sites.

Use `ONENOTE_SITE_PATHS` to add exact SharePoint site URLs or a tenant base URL such as `https://contoso.sharepoint.com`.

SharePoint site discovery requires Microsoft Graph permission `Sites.Read.All`. Notebook writing requires OneNote permissions.

## Sections

After choosing a notebook, the app loads its sections. The last selected section is remembered per notebook.

## Visibility differences

Different users may see different notebooks because OneNote visibility depends on SharePoint site permissions, shared notebook permissions, and the exact tenant URL that is configured.
