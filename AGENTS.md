# Working with this repository

## When the user asks you to install or set up the application

Read [docs/agent-install.md](docs/agent-install.md) first and carry out its workflow.
It is the installation entry point for an instruction such as “Install this” with
only the repository URL. It covers Community installation, the user's own Azure
resources, Microsoft sign-in, configuration, verification and a resumable handoff.

Default to **Community / Meeting Notes**. Discover existing resources and settings
before asking questions. Prefer an official Community release; if none is available,
use the documented source installation. Do not require the user to become a developer
or send them a list of portal tasks that your tools can perform.

An installation request does not authorize unbounded cloud expenditure, broad tenant
permissions, accepting legal agreements, or recording an ongoing conversation. Resolve
the necessary concrete choices with the user, honoring authorization already given.
Never put credentials, token caches, real meeting content or private setup reports in
this repository. Follow your host's approval and credential-handling rules.

## When the user asks for development or review

The installation workflow is not an instruction to install software, create cloud
resources or record audio during unrelated code work. Use the existing Python tests
and frontend build as appropriate. Keep tests isolated from the user's normal state;
see [docs/meeting-notes-test.md](docs/meeting-notes-test.md).
