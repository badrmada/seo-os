# Security

## Reporting a vulnerability

**Do not open a public issue.** Use GitHub's private vulnerability reporting —
[**Report a vulnerability**](https://github.com/badrmada/seo-os/security/advisories/new),
under the repository's Security tab. That opens a private thread with the
maintainers and a draft advisory in the same place.

There is one maintainer, so expect a first response in days rather than hours. If
a report goes a week without acknowledgement, it has been missed rather than
ignored — say so on the same thread.

Useful in a report: what an attacker gets, and the shortest path you know of to
reproduce it. A tenant folder or a config that triggers the behaviour is worth
more than a description of it.

## Scope

This repo is a runtime plus the things that ship it: the agent in
[`services/seo-agents/`](services/seo-agents/), the [Helm
chart](helm-charts/seo-os/), the [Compose stack](deploy/compose/), and the CI in
[`.github/workflows/`](.github/workflows/).

Worth reporting:

- Anything that lets one tenant's run read or write another tenant's data.
  Tenants are mounted at `/userdata` and are the main trust boundary here.
- Anything that turns tenant-supplied config, a prompt, or a tool result into
  code execution, a file write outside the tenant folder, or an outbound request
  to a host the operator did not configure.
- Credential leakage: API keys reaching logs, error messages, the transcript, or
  a built image layer.
- A supply-chain hole in the build — a workflow that would run a fork's code with
  this repo's token, or publish an image from an unreviewed commit.

Out of scope, because they are the documented design rather than a flaw:

- **CI does not deploy, and that is deliberate.** No workflow holds host
  credentials; see the header of [`images.yml`](.github/workflows/images.yml).
- The agent makes outbound calls to the LLM and search providers an operator
  configures. Configuring a provider means trusting it.
- Anything requiring an operator's own cluster credentials, host shell, or
  `.env` file. If you already have those, the run was yours.
- The quality or accuracy of generated SEO content.

## What operators should know

- **Pin what you deploy.** `latest` is a moving tag by definition. Every build
  also publishes `sha-<commit>`, which is the one to use anywhere the answer to
  "what ran?" matters. See [`deploy/README.md`](deploy/README.md).
- **Keys arrive by environment or Secret, never baked into an image.** The chart
  renders them into a `Secret`; Compose reads a `.env` that is gitignored. An
  image with a key in it is a bug worth reporting.
- Images publish to GHCR only from `main` and `v*` tags, and only after the test
  suite and documentation check pass. A pull request builds and never pushes.
