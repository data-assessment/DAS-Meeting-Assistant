# Desktop release checks

The application's source uses Apache-2.0. Preserve LICENSE, NOTICE and source attribution;
the license does not require a permanent logo in derivative user interfaces.
Third-party components retain their original conditions.

Before distributing a binary:

- Verify dependency/document hashes with `packaging/third_party.py` and the complete
  recipient agreement with `packaging/prepare_microsoft_terms.py`.
- Read [Microsoft component distribution](../third_party/MICROSOFT-REDISTRIBUTION.md).
  Each distributor needs its own applicable distribution rights. Internal entitlement
  evidence must not be bundled with the client.
- Keep LGPL/MPL source material and replacement instructions with the distribution.
  The Rust notice collection is a documented dependency superset, not a complete binary SBOM.
- Validate the selected deployment profile. Never package credentials, real recordings,
  user token caches, private operations documents or unreviewed local configuration.
- Scan tracked files and all publishable Git refs for secrets and internal-only material.
- Build Community and any intended managed variant; run Python, browser and packaged
  runtime/notes/HTTP checks. Complete clean-Windows onboarding with authorized cloud resources.
- Use fixed, verified native runtime inputs; prefer a signed release. Publish hashes,
  source version and actual validation limits alongside each release.

Release-specific operational evidence and organization-owned profiles are maintained by
the distributor outside this source repository. General build instructions are in
[packaging/README.md](../packaging/README.md).
