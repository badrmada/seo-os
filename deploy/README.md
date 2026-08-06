# Deploying SEO-OS

Two places, one shape:

- **[`compose/`](compose/)** — one host, Docker Compose.
- **[`../helm-charts/seo-os/`](../helm-charts/seo-os/)** — a cluster, where one
  run is one Kubernetes `Job`.

> **There is no long-running service in either yet.** The runtime is a CLI: a
> process that does one run and exits. Compose deploys Redis — so a run's state
> is readable from outside the process producing it — plus the agent as a
> *one-shot* container, run on demand; the chart renders a Secret and a Job and
> nothing else. The thing that turns a run into something you request over HTTP
> is the gateway, [step 3 of the roadmap](../docs/roadmap.md#3-the-gateway-the-api-handler),
> and it is what will bring the first Deployment with it.

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

**The package is currently private, and a cluster is where you find that out.**
GHCR creates a package private on its first push regardless of the repository's
visibility, and a successful `docker push` cannot tell you — the machine that
pushed is authenticated. A `helm install` then sits in `ImagePullBackOff` with a
403 from a token endpoint, which is a long way from the setting that caused it.

Ask the registry the question a cluster asks, before installing anything:

```bash
make -C services/seo-agents pullable        # or: make publish, which ends with it
```

Two fixes, and the target prints both: flip the package to **Public** (GitHub →
your Packages → `seo-agents` → Package settings → Change visibility), or keep it
private and give the cluster a credential —

```bash
kubectl create secret docker-registry ghcr --docker-server=ghcr.io \
  --docker-username=<you> --docker-password=$GITHUB_TOKEN
helm upgrade --install seo-os ./helm-charts/seo-os --set imagePullSecrets[0].name=ghcr
```

Building locally is still one command, and is what the Compose file falls back to
if you'd rather not pull:

```bash
docker build -t seo-agents:local services/seo-agents
docker run --rm seo-agents:local --help
```

Publishing one by hand — a registry of your own, or a build CI hasn't made yet —
is `make -C services/seo-agents publish`, which logs in with `$GITHUB_TOKEN`,
pushes both the version tag *and* `latest` (the tag Compose and the chart
default to), and finishes with the pull check above. `IMAGE_REPO=` points it at
any other registry.

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

## A cluster: one run, one Job

```bash
helm install seo-os ./helm-charts/seo-os
kubectl logs -f job/seo-os-acme-r1
```

That installs on any cluster with no credentials, no storage class and no
ingress: the default tenant names no provider, so the run uses the mock LLM and
writes its result JSON to stdout. The run *is* the log.

The same tenant folder Compose bind-mounts arrives here as a **Secret** — the
runtime does no `${VAR}` expansion, so `tenant.json` holds literal API keys and
the whole file is the credential. `--set-file` ships the real one straight from
the folder you already run locally, without it entering a values file or a
commit:

```bash
helm upgrade --install seo-os ./helm-charts/seo-os \
  --set tenant.name=echooers \
  --set-file tenant.configJson=services/seo-agents/userdata/echooers/tenant.json \
  --set-file tenant.inputJson=services/seo-agents/userdata/echooers/input.json
```

**[`helm-charts/seo-os/README.md`](../helm-charts/seo-os/README.md) is the full
walkthrough**: the three ways a tenant folder can reach the pod (a Secret, an RWX
volume, an init container syncing from S3 or git) and when each is right, why
plugins that need extra dependencies belong in a per-tenant image, where a result
goes when the pod is gone, and the four Job settings that carry a real decision.

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
