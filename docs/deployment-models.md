# Desktop deployment profiles

Community and organization-managed distributions use the same client source.
The [profile validator](../deployment_profile.py) is the source of truth for allowed
fields. Profiles contain only public IDs, HTTPS endpoints, model names and defaults.
Keys, passwords, tokens, unknown fields and URLs containing credentials are rejected.

## Community

Build with [community.json](../packaging/profiles/community.json). The operator supplies
their own Azure Speech resource, text-model deployment and public desktop Microsoft
application registration. Runtime credentials are configured after installation;
they never belong in an installer. Calendar and OneNote permissions are requested
for the corresponding features; optional chat/contact discovery is a separate consent.

Meeting Notes uses Speech for live audio and a chat deployment for summaries.
The legacy batch-recording flow has separate transcription settings. Infrastructure
provisioning and an agent-guided clean-machine setup remain separate release work.

## Organization-managed direct Entra access

Use [managed-entra.example.json](../packaging/profiles/managed-entra.example.json) as
the schema example. An administrator creates the actual profile outside this repository,
with a fixed tenant, public desktop client ID, Speech endpoint and text-model deployment.
No client secret is required. Azure resource access is enforced by Microsoft identity
and resource-scoped role assignments, not by the client UI or possession of the profile.

Pass that private profile to `packaging/build.ps1 -Profile <profile.json>`.
The installer embeds its validated public connection settings. User sign-in happens
after installation; user tokens and existing settings are never bundled.

## Optional gateway profile

[managed.example.json](../packaging/profiles/managed.example.json) describes a separate
gateway routing mode. Such a deployment requires an independently operated compatible
service. Its server implementation, secrets, entitlements and infrastructure are not
part of this desktop repository. Direct Entra mode does not use gateway fallback routing.

## Isolation and upgrades

Community and managed installations have separate installer identities, executables,
data directories and default ports. Existing technical installation identities are kept
stable for upgrades. For isolated tests set only `VOICE_TRANSCRIBER_DATA_ROOT`; changing
`LOCALAPPDATA` also changes the external browser profile and is not a sign-in test strategy.
