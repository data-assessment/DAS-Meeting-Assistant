# Agent runbook: the user's Microsoft and Azure resources

Companion to [agent-install.md](agent-install.md), not a separate request to provision
anything. Perform the cloud steps with the user's authorized CLI, connector or browser.
If the user has supplied only “install this”, discover what already exists and prepare
the local application before asking for missing account, cost or permission decisions.

## Resource contract

The Community Meeting Notes path needs:

1. A resource usable by the **Azure Speech SDK ConversationTranscriber** with a
   resource key and region. No separate Whisper/batch-transcription deployment is
   needed for this live path.
2. An Azure resource exposing the OpenAI **v1 chat completions** API and a deployed
   text model. The application sends `response_format={"type":"json_object"}`,
   `max_completion_tokens=12000` and `store=false`, with system/user messages. Check
   that the selected model supports this actual request and has sufficient output
   capacity; a model name alone is not evidence of compatibility.
3. For Teams presence, Outlook and OneNote: a Microsoft 365 work/school identity
   and a **public desktop app registration in the user's tenant**, using delegated
   Graph permissions. This is independent of resource-key authentication to Azure.

An existing Foundry resource may be suitable, but a Foundry project URL, portal URL,
agent deployment or Azure account by itself is not a working chat deployment.
The simplest fresh configuration is a Speech resource and an Azure OpenAI resource
with a compatible chat deployment. Reuse a suitable existing configuration instead
of duplicating resources. Keep all actual account/resource details outside the repo.

## A. Discover and resolve the plan

Check Azure login first; initiate login only when needed. Browser account selection,
MFA and tenant policy may need the user. Azure subscription ownership does not imply
permission to register an Entra application or grant tenant-wide consent.

Example read-only PowerShell/CLI inventory (use equivalent supported tools if needed):

```powershell
az account show --query '{subscription:id,name:name,tenant:tenantId}' -o json
az account list --query '[].{subscription:id,name:name,tenant:tenantId,isDefault:isDefault}' -o json
# Resolve the selected subscription and assign its actual ID before continuing.
az cognitiveservices account list --subscription $subscriptionId --query '[].{name:name,group:resourceGroup,kind:kind,region:location,endpoint:properties.endpoint}' -o json
az cognitiveservices account deployment list --subscription $subscriptionId --resource-group $textResourceGroup --name $textResourceName -o json
```

Inspect only the selected scope after discovery. A missing local `az` binary is not
proof that the user has no cloud access: use their available connector/browser or
install the official Azure CLI if needed. Confirm tenant/account identity before
mutation. Do not silently use whichever subscription happens to be the CLI default.

Before new paid resources or a new deployment, present a concrete plan: subscription,
resource group, region, resource names/types, model version, deployment type/capacity,
and expected pricing basis. Obtain the user's spending authorization if it is not
already clear. A quota/capacity setting is **not** a monthly spending cap. Avoid
provisioned throughput for a first personal installation unless expressly requested.
Keep any agreed budget alerts distinct from a hard cap; neither the client nor this
runbook implements a hard monthly cutoff.

For missing organizational permissions, prepare the exact request for the responsible
administrator, including the app ID and scopes. Do not request passwords, a Global
Administrator account, blanket application permissions or disabled Conditional Access.

## B. Reuse or provision Speech and the text model

For existing resources, verify region, endpoint, supported model deployment, quota,
key-auth availability and connectivity from the desktop. If the organization's policy
disables API-key authentication, do not turn it on as a workaround. Community's current
Meeting Notes UI expects keys; an administrator-managed Entra deployment is a different
route described in [deployment-models.md](deployment-models.md).

For an approved fresh setup, these are command templates. Resolve every variable
from discovery/approval and check native exit codes before running the next command.
Query existing names/IDs first and reuse them on resume; do not overwrite a resource
or recreate a deployment just because its name is familiar.

```powershell
# $subscriptionId, $resourceGroup, $location and resource names are resolved first.
az group create --subscription $subscriptionId --name $resourceGroup --location $location -o none
az cognitiveservices account create --subscription $subscriptionId --resource-group $resourceGroup --name $speechResourceName --kind SpeechServices --sku S0 --location $location -o none
az cognitiveservices account create --subscription $subscriptionId --resource-group $resourceGroup --name $textResourceName --kind OpenAI --sku S0 --location $location --custom-domain $textResourceName -o none

# Discover actual model/version/SKU availability instead of copying a stale model name.
az cognitiveservices account list-models --subscription $subscriptionId --resource-group $resourceGroup --name $textResourceName -o json
# Only after selecting a supported model and the approved capacity/deployment type:
az cognitiveservices account deployment create --subscription $subscriptionId --resource-group $resourceGroup --name $textResourceName --deployment-name $deploymentName --model-format OpenAI --model-name $modelName --model-version $modelVersion --sku-name $deploymentSku --sku-capacity $deploymentCapacity -o none
```

If the chosen region/model has no quota, propose a supported alternative within the
approved cost and data-location constraints. Do not silently switch to a Global
deployment or another region. If network policy uses private endpoints, use the approved
VPN/network route instead of opening the resource publicly.

Read the final endpoint and deployment from the resource, not from a naming guess.
Supply the **root** endpoint to Meeting Notes, for example
`https://<name>.openai.azure.com`. The application appends `/openai/v1/`. Its validator
also accepts root hosts ending in `.cognitiveservices.azure.com` and
`.services.ai.azure.com`; acceptance by the validator does not prove that the service
implements the needed API. Do not supply `/openai/deployments/...`, `/openai/v1/`,
a project path, a query string or an APIM gateway in this field.

Retrieve keys only inside a secret-aware local process using the exact selected
resource ID, for example by capturing the result of
`az cognitiveservices account keys list` without printing it. Transfer the Speech key
and text-model key directly to the local configuration request in
[installation step 5](agent-install.md#5-launch-and-configure-meeting-notes).
Do not put key values in generated scripts, command arguments, process-wide persistent
environment variables, the setup report or the model conversation. If the available
tool cannot avoid exposing them, use the app's masked key fields as the user handoff.
Do not rotate existing resource keys as part of installation.

## C. Register the desktop identity

Use a suitable existing public desktop app if the user/administrator authorizes it.
Otherwise create one in the confirmed tenant with:

- A recognizable display name such as `DAS Meeting Assistant Community`.
- Single-tenant organizational accounts by default.
- The **Mobile and desktop applications** platform and redirect URI `http://localhost`
  for system-browser interactive sign-in. This OAuth callback is separate from the
  application's local UI port; do not replace it with `http://localhost:8766`.
- Delegated Microsoft Graph scopes for the selected features below.
- No client secret, certificate credential, web/SPA implicit flow or application permissions.

The source uses an MSAL public client and authorization code + PKCE for
`GRAPH_AUTH_MODE=interactive`. Device-code flow is an optional alternative subject
to tenant policy, not a workaround for a policy blocking interactive sign-in. Enable
fallback/public-client device flows only when intentionally using that alternative.

After checking the current CLI login's tenant, an approved creation can use:

```powershell
$clientId = az ad app create --display-name 'DAS Meeting Assistant Community' --sign-in-audience AzureADMyOrg --public-client-redirect-uris 'http://localhost' --query appId -o tsv
if ($LASTEXITCODE -ne 0) { throw 'Desktop app registration failed' }
```

Record both the app's application/client ID and object ID privately. Configuration
uses the **application/client ID**, not the object ID, resource ID or subscription ID.
Set `GRAPH_TENANT_ID` to the confirmed tenant GUID. Verify the created or reused app's
platform/redirect and account types rather than inferring them from its display name.

### Delegated permissions by feature

| Feature | Scopes used by this code | Default setup action |
| --- | --- | --- |
| Teams presence / main Graph login | `Presence.Read` | Configure for Teams integration. |
| Meeting Notes Outlook title/invitees | `Calendars.Read` | Configure for calendar context; connect from the Notes UI. |
| Meeting Notes OneNote | `User.Read`, `Notes.ReadWrite.All` | Only when OneNote is requested. Includes notebooks accessible to the signed-in user, including shared ones. |
| SharePoint site notebook discovery | Above plus `Sites.Read.All` | Only for requested/configured site discovery. |
| Optional Teams chat-person suggestions | `Chat.Read` | Leave out unless requested. Names are candidates, not voice identification. |
| Legacy attendance/transcript retrieval | `OnlineMeetings.Read`, `OnlineMeetingArtifact.Read.All`, `OnlineMeetingTranscript.Read.All` and calendar scope | Not required by this runbook; keep `FETCH_ATTENDEES=false` and `USE_TEAMS_TRANSCRIPT=false`. |

The Notes OneNote implementation requests `Notes.ReadWrite.All`, not the narrower
`Notes.ReadWrite` used by the separate legacy export. Do not substitute one and claim
the current implementation is configured. If the organization's policy rejects that
scope, keep local notes working and report OneNote as unavailable pending approval.

For CLI automation, obtain permission GUIDs from the target tenant's Microsoft Graph
service principal instead of inventing or copying them from an old setup:

```powershell
$graphAppId = '00000003-0000-0000-c000-000000000000'
$graphScopes = az ad sp show --id $graphAppId --query oauth2PermissionScopes -o json | ConvertFrom-Json
if ($LASTEXITCODE -ne 0) { throw 'Could not read delegated Graph scopes' }
# $approvedScopes contains only the feature scopes agreed for this installation.
foreach ($scopeName in $approvedScopes) {
    $scope = @($graphScopes | Where-Object { $_.value -eq $scopeName -and $_.isEnabled })
    if ($scope.Count -ne 1) { throw "Delegated scope unavailable: $scopeName" }
    az ad app permission add --id $clientId --api $graphAppId --api-permissions "$($scope[0].id)=Scope" -o none
    if ($LASTEXITCODE -ne 0) { throw "Could not configure scope: $scopeName" }
}
```

On resume, read existing permissions and add only missing approved scopes. Declaring
permissions on the app is not consent. Have the user complete the application's own
interactive sign-in and feature-specific consent. Some tenants require administrator
approval even for scopes normally user-consentable. Do not blanket-grant admin consent
to everything registered on an existing shared application. Azure CLI sign-in also
does not sign the desktop app into Graph; they use different clients/token caches.

## D. Validate without private meeting data

Use the app's actual request format with invented short meeting content when testing
the text model. A 200 response from an unrelated playground/API is not enough: require
a final JSON draft accepted by the application. A low-cost synthetic request can be
made as part of the already-approved resource test; do not replay real stored notes.

Speech must be checked with the current SDK, correct region/key, selected microphone
and Teams loopback. Complete the user-authorized short audio test from the main runbook;
never open a microphone or transcribe an existing call as an implicit configuration probe.
Check selected Graph features using the signed-in user's own allowed data. Do not
create invitations or change calendar events to establish read access.

Persist only configuration metadata in the private setup record, including which
resources were reused vs created and who controls them. Return to
[installation step 4](agent-install.md#4-prepare-safe-first-run-state) once values are
ready. Distinguish “resource provisioned”, “configured”, and “tested end to end”.

## Official references

Consult the current docs if a CLI flag, model, permission or portal label differs.
The app's code remains the contract for what it can actually consume.

- [Register an Entra application](https://learn.microsoft.com/en-us/entra/identity-platform/quickstart-register-app)
- [Desktop app configuration](https://learn.microsoft.com/en-us/entra/identity-platform/scenario-desktop-app-configuration)
- [Azure CLI app registration](https://learn.microsoft.com/en-us/cli/azure/ad/app)
- [Graph delegated permissions](https://learn.microsoft.com/en-us/graph/permissions-reference)
- [Speech SDK setup](https://learn.microsoft.com/en-us/azure/ai-services/speech-service/get-started-speech-to-text)
- [Azure AI resource CLI](https://learn.microsoft.com/en-us/cli/azure/cognitiveservices/account)
- [Azure OpenAI deployment setup](https://learn.microsoft.com/en-us/azure/cognitive-services/openai/how-to/create-resource)
- [Azure OpenAI JSON mode](https://learn.microsoft.com/en-us/azure/foundry/openai/how-to/json-mode)
- [Azure OpenAI v1 request reference](https://learn.microsoft.com/en-us/azure/foundry/openai/latest)

Local code references: [Graph](../engine/graph_auth.py), [calendar](../engine/notes_people.py),
[OneNote](../engine/notes_onenote.py), [chat-person candidates](../engine/notes_calls.py),
[summary request](../engine/meeting_notes.py), [Speech session](../engine/speech/session.py).
