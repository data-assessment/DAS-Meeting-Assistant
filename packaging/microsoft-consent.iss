// Shared by both production installers and the isolated consent test fixture.
// Built-in LicenseFile renders the complete offline text. Never restore consent
// from a previous installation or infer it from /SILENT or /SUPPRESSMSGBOXES.
function MicrosoftTermsSwitchAccepted: Boolean;
var
  I, Found: Integer;
  Argument, Prefix: String;
begin
  Found := 0;
  Result := False;
  Prefix := '/ACCEPTMICROSOFTTERMS=';
  for I := 1 to ParamCount do begin
    Argument := ParamStr(I);
    if CompareText(Copy(Argument, 1, Length(Prefix)), Prefix) = 0 then begin
      Found := Found + 1;
      if Copy(Argument, Length(Prefix) + 1, MaxInt) <> '{#MicrosoftTermsRevision}' then
        Exit;
    end;
  end;
  Result := Found = 1;
end;

function MicrosoftTermsAccepted: Boolean;
begin
  if WizardSilent then
    Result := MicrosoftTermsSwitchAccepted
  else
    Result := WizardForm.LicenseAcceptedRadio.Checked;
end;

<event('InitializeSetup')>
function RequireSilentMicrosoftConsent: Boolean;
begin
  Result := not WizardSilent or MicrosoftTermsSwitchAccepted;
  if not Result then begin
    Log('MICROSOFT_TERMS_REQUIRED: /ACCEPTMICROSOFTTERMS={#MicrosoftTermsRevision}');
    SuppressibleMsgBox(CustomMessage('MicrosoftConsentRequired'), mbError, MB_OK, IDOK);
  end;
end;

<event('InitializeWizard')>
procedure ResetMicrosoftConsent;
begin
  WizardForm.LicenseAcceptedRadio.Checked := False;
  WizardForm.LicenseNotAcceptedRadio.Checked := True;
end;

<event('NextButtonClick')>
function CheckMicrosoftConsentPage(CurPageID: Integer): Boolean;
begin
  Result := True;
  if CurPageID = wpLicense then
    Result := MicrosoftTermsAccepted;
end;

<event('PrepareToInstall')>
function GuardMicrosoftConsent(var NeedsRestart: Boolean): String;
begin
  Result := '';
  if not MicrosoftTermsAccepted then
    Result := CustomMessage('MicrosoftConsentRequired');
end;

<event('CurStepChanged')>
procedure RecordMicrosoftConsent(CurStep: TSetupStep);
var
  Method, TermsHash, Receipt: String;
begin
  if CurStep <> ssPostInstall then Exit;
  if WizardSilent then Method := 'explicit-command-line' else Method := 'interactive';
  if ActiveLanguage = 'german' then TermsHash := '{#MicrosoftTermsHashDE}'
  else TermsHash := '{#MicrosoftTermsHashEN}';
  Receipt := 'terms_revision={#MicrosoftTermsRevision}' + #13#10 +
             'terms_sha256=' + TermsHash + #13#10 +
             'application_version={#AppVersion}' + #13#10 +
             'language=' + ActiveLanguage + #13#10 +
             'method=' + Method + #13#10 +
             'accepted_at_local=' + GetDateTimeString('yyyy-mm-dd hh:nn:ss', '-', ':') + #13#10;
  if not SaveStringToFile(ExpandConstant('{app}\microsoft-terms-acceptance.txt'), Receipt, False) then
    Log('Could not write local Microsoft terms receipt.');
  Log('MICROSOFT_TERMS_ACCEPTED: revision={#MicrosoftTermsRevision}; method=' + Method + '; sha256=' + TermsHash);
end;
