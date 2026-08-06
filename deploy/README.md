# Deploying SEO-OS

**[`compose/`](compose/)** — one host, Docker Compose. That is the whole of it
today, on purpose.

Two things to know before the instructions, because both change what this folder
means:

> **There is no long-running service here yet.** The runtime is a CLI. What this
> deploys is Redis — so a run's state is readable from outside the process
> producing it — plus the agent as a *one-shot* container, run on demand. The
> thing that turns a run into something you request over HTTP is the gateway,
> [step 3 of the roadmap](../docs/roadmap.md#3-the-gateway-the-api-handler).
>
> **The cluster story will be a Helm chart, and isn't written.** Raw Kubernetes
> manifests were here briefly and were removed rather than kept as a second thing
> to maintain: a chart is what people actually install, and half a chart's worth
> of loose YAML is not a step towards one. See
> [step 2](../docs/roadmap.md#2-deployment-compose-now-a-helm-chart-later).

## Where the images come from

Nowhere yet, automatically:
[`images.yml.disabled`](../.github/workflows/images.yml.disabled) is written but
parked — the build isn't ready to run on every push. Only `tests.yml` and
`docs.yml` are live. Until it is enabled, build locally:

```bash
docker build -t seo-agents:local services/seo-agents
docker run --rm seo-agents:local --help
```

When it *is* enabled it publishes to GHCR on `main` and on a `v*` tag, as
`ghcr.io/badrmada/seo-os/seo-agents:{latest,v1.2.0,sha-<commit>}`. Prefer the
`sha-` tag anywhere you care what is running; `latest` is a moving target by
definition.

## One host: Docker Compose

```bash
cd deploy/compose
cp .env.example .env          # image tag, where userdata lives
docker compose up -d          # Redis
docker compose run --rm agent run --tenant acme
```

`up` starts Redis and nothing else on purpose: the agent sits behind a `run`
profile, because Compose restarting a finished CLI forever is not a deployment.

The tenant workspace is **mounted, never baked in** — one image serves every
tenant under `/userdata`, and no tenant's credentials enter an image layer. Point
`USERDATA_PATH` at the folder that holds them.

The gateway's shape is already in the compose file, commented out — a service, a
port, a health check — so that step is configuration rather than a rewrite here.

## Rolling out a new image

[`deploy.yml.disabled`](../.github/workflows/deploy.yml.disabled): manually
triggered, one input for the image tag, `docker compose pull && up -d` over SSH.
Parked along with the build that would produce the image it rolls out.

It needs `DEPLOY_HOST`, `DEPLOY_USER`, `DEPLOY_SSH_KEY` and `DEPLOY_KNOWN_HOSTS`
(optionally `DEPLOY_PATH`), and **fails by name before touching anything** when
one is missing — a half-deploy is harder to explain than a job that refused to
start. `DEPLOY_KNOWN_HOSTS` is `ssh-keyscan <host>`: the host key is pinned
rather than trusted on first use, because a deploy is precisely when that
distinction earns its keep.

It ships **no files**. The compose file, the `.env` and the tenant folders live
on the host, since two of those three hold credentials.
