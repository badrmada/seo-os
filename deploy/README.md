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
> of loose YAML is not a step towards one. How a run becomes a Job, and how a
> tenant's folder and plugins reach that Job's pod, is planned in
> [step 2](../docs/roadmap.md#kubernetes-how-a-run-becomes-a-job).

## Where the images come from

[`images.yml`](../.github/workflows/images.yml), on every push to `main` and
every `v*` tag, as
`ghcr.io/badrmada/seo-os/seo-agents:{latest,v1.2.0,sha-<commit>}`. Prefer the
`sha-` tag anywhere you care what is running; `latest` is a moving target by
definition.

A pull request builds the image and never publishes it — a fork's PR would
otherwise be pushing to this project's registry — so a PR proves the build and a
merge produces the artifact. What `main` publishes is `linux/amd64` and
`linux/arm64`; a PR only proves amd64, because arm64 under QEMU costs real time
to prove something a merge will prove again.

The package is **public**, so nothing below needs a `docker login`. (GHCR creates
a package private on its first push regardless of the repository's visibility —
if a pull ever 404s for an anonymous client, that setting is the first thing to
check, under the repo's Packages settings.)

Building locally is still one command, and is what the Compose file falls back to
if you'd rather not pull:

```bash
docker build -t seo-agents:local services/seo-agents
docker run --rm seo-agents:local --help
```

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

**[`compose/README.md`](compose/README.md) is the full walkthrough**: what is
persistent and where, how a tenant actually opts into that Redis, and — since
Redis here is the example rather than the requirement — how to point a run at a
managed one, at no infrastructure at all (`state_provider: "file"`, into the
folder you already mounted), or at a store of your own.

## Rolling out a new image

On the host, and by hand:

```bash
docker compose pull
docker compose up -d --remove-orphans
```

**No CI job does this**, and there is no parked one waiting to. Automating it
would mean this repository holding an SSH key, a host address and the ability to
restart a deployment — to automate a decision (*run this build*) that is separate
from the one CI already makes (*this build is good*). CI ends at publishing the
image; choosing to run one is yours. The compose file, the `.env` and the tenant
folders stay on the host anyway, since two of those three hold credentials.
