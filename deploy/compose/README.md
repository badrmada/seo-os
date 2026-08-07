# Docker Compose — one host, and state that outlives the run

[`docker-compose.yml`](docker-compose.yml) is the whole single-host deployment:
**Redis**, so a run's progress is readable by a process that isn't executing it,
and the runtime as a one-shot container with a tenant folder mounted into it.

```bash
cd deploy/compose
cp .env.example .env                          # image tag, where userdata lives
docker compose up -d                          # Redis
docker compose run --rm agent run --tenant acme
```

Redis is **the example, not the requirement** — see
[bring your own provider](#bring-your-own-provider-redis-is-just-the-example)
below. The runtime names a provider in `tenant.json`; this file's only job is to
make the thing that config names reachable.

## Two facts that shape this file

**The runtime is a CLI, not a server.** There is nothing to `up` here except
Redis. The agent sits behind a Compose *profile* (`profiles: ["run"]`) so
`docker compose up` doesn't start it, because Compose restarting a finished CLI
forever is not a deployment — you reach it with `docker compose run --rm agent …`
instead. The first long-running service is the gateway,
[step 1 of the roadmap](../../docs/roadmap.md#1-the-gateway); its
shape is already in the file, commented out, so that step is configuration rather
than a rewrite.

**A tenant is a folder**, mounted rather than baked into the image. One image
serves every tenant under `/userdata`, and no tenant's credentials ever enter an
image layer. That is why the interesting line in the file is a bind mount and not
a `COPY`.

## What is persistent, and where

Two different persistent things, deliberately stored two different ways:

| What | Where it lives | Why that way |
|---|---|---|
| **The tenant workspace** — `tenant.json`, `plugins/`, `templates/`, `data/`, `output/` | A **bind mount** from `USERDATA_PATH` on the host to `/userdata` | You edit these. A bind mount means editing a prompt on the host is editing it in the container, and adding a tenant is `mkdir`. It also holds API keys, which is the reason it is neither in the image nor in this repo. |
| **Run state** — one snapshot per `run_id`, rewritten after every step | The `redis-data` **named volume**, via Redis with `--appendonly yes` | Nothing edits these by hand; they are written by one process and read by another. A named volume is Docker's business, not yours. |

Everything else is disposable. The container has no state of its own: kill it
mid-run and you lose the run, not the tenant.

Redis is published on `127.0.0.1:${REDIS_PORT:-6379}` — **loopback on purpose.**
It is a state store for processes on this host, not a public service, and it has
no password in this file. If you bind it to `0.0.0.0`, set `requirepass` in the
same edit.

## Turning the Redis on

Bringing Redis up does nothing by itself. **A run uses it only when a tenant asks
for it**, in that tenant's `tenant.json`:

```jsonc
{
  "state_provider": "redis",
  "state_options": {
    "url": "redis://redis:6379/0",
    "key_prefix": "seo-agent:run:",
    "ttl_seconds": 604800
  }
}
```

Three things about that snippet are Compose-specific:

- **`redis` is a hostname**, and it is the Compose *service name*. Containers on
  this file's default network resolve each other by it. From the host itself the
  same instance is `redis://localhost:6379/0` — which is what a `python
  src/main.py run` outside Docker should use.
- **Config values are literal.** A `tenant.json` is not shell: `"${REDIS_URL}"`
  stays the string `${REDIS_URL}`. The `REDIS_URL` variable the compose file sets
  on the agent container is there for plugin code that reads its own environment,
  and for the gateway when it exists — it is not a way to keep the URL out of the
  config. Write the URL.
- **`ttl_seconds` is worth setting on anything long-lived.** Nothing else expires
  a snapshot; `0` (the default) means these keys accumulate until you delete
  them.

Without those lines a run uses the default `memory` store, finishes, prints its
JSON, and leaves Redis empty — which is a correct, if quiet, deployment of a CLI.
Full field reference:
[`state_provider`](../../services/seo-agents/docs/configuration.md#where-the-runs-state-is-kept-state_provider).

## Bring your own provider — Redis is just the example

Redis is in this file because it is the thing the gateway and the frontend will
both read a live run through, and because one `image:` line is the cheapest
honest demonstration of "a run is watchable from outside the process." It is not
a dependency of the runtime. Pick whichever of these matches what you already
run:

**A managed Redis** (Upstash, ElastiCache, Memorystore, a Redis your ops team
already operates). Delete the `redis` service and the `depends_on` block, and
point the URL at it — TLS included, since `rediss://` is a URL scheme and not a
separate provider:

```jsonc
{
  "state_provider": "redis",
  "state_options": { "url": "rediss://:PASSWORD@redis.example.com:6380/0", "ttl_seconds": 604800 }
}
```

**No infrastructure at all.** `state_provider: "file"` writes one atomic
`<run_id>.json` per run into the tenant's own folder — which, in this file, is a
bind mount, so those snapshots are already persistent on the host and already
visible to anything else running there. That is a complete answer for a single
host, and it removes a service:

```jsonc
{ "state_provider": "file", "state_options": { "path": "state" } }
```

**Something else entirely** — Postgres, DynamoDB, the job table your application
already has. `state_provider: "custom"` with a class in the tenant's `plugins/`
folder; three methods, and it may be sync or async. The walkthrough is
[extending.md](../../services/seo-agents/docs/extending.md#walkthrough-a-state-store-of-your-own).
If that store is a container too, it joins this file exactly the way `redis`
does: add the service, and name it from `tenant.json` by its service name.

The same shape applies to every other provider in a config, not just state. If
your LLM is a local Ollama or vLLM rather than Gemini, it becomes one more
service here and a `llm_provider: "custom"` in the tenant config pointed at
`http://ollama:11434` — Compose makes it reachable, the config chooses it, and
nothing in this repo needs to know. The provider menu is
[configuration.md](../../services/seo-agents/docs/configuration.md).

## Running a tenant

```bash
docker compose run --rm agent run --tenant acme
docker compose run --rm agent run --tenant acme --input input.json -v
docker compose run --rm agent list-tenants
docker compose run --rm agent check-data --tenant acme
```

`--rm` because each of these is a finished container the moment it prints. There
is no `--userdata` flag in any of them: the image sets
`SEO_AGENT_USERDATA=/userdata`, so mounting a folder there *is* the flag. Paths
inside a config — `--input`, a template file, a data fixture — resolve inside the
tenant's folder, so nothing in a `tenant.json` needs to know it is in a container.

**Where the result goes** is the tenant's choice and not this file's:
`output_sinks` defaults to stdout, which here means the container's logs. A
`file` sink writes into the mounted tenant folder, so it lands on the host; a
`webhook` sink posts out of the container and needs nothing mounted at all. See
[output sinks](../../services/seo-agents/docs/configuration.md#where-the-result-goes-output-sinks).

**Scheduling** is the host's job for now — `cron` calling `docker compose run` is
the entire supported answer, and it is a real one for a handful of tenants.
Fifty tenants is a queue, which is the gateway's
([step 1](../../docs/roadmap.md#1-the-gateway)), and a cluster's
`CronJob` is [step 4's chart
work](../../docs/roadmap.md#4-the-chart-past-a-single-job).

## The `.env`

Every value in [`.env.example`](.env.example) has a working default in the
compose file; the file exists to name what is worth changing.

| Variable | Default | What it decides |
|---|---|---|
| `IMAGE_REPO` | `ghcr.io/badrmada/seo-os` | Where images come from. Yours, if you build your own. |
| `IMAGE_TAG` | `latest` | Which one. Prefer a `sha-` tag anywhere you care what is running. |
| `USERDATA_PATH` | `./userdata` | The tenant workspace on the host. Holds real credentials — keep it out of git. |
| `REDIS_PORT` | `6379` | The loopback port Redis is published on. |

## Rolling out a new image

```bash
docker compose pull
docker compose up -d --remove-orphans
```

**CI does not do this for you, on purpose.** Automating it would mean this
repository holding an SSH key, a host address and the ability to restart your
deployment — to automate a decision (*run this build*) that is separate from the
one CI already makes (*this build is good*). The compose file, the `.env` and the
tenant folders stay on the host, since two of those three hold credentials. What
CI does end at is publishing the image:
[images.yml](../../.github/workflows/images.yml), and
[where the images come from](../README.md#where-the-images-come-from).
