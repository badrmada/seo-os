# SEO-OS on Kubernetes

One run is one `Job`. The runtime is a process that does one run and exits, which
is precisely the workload Kubernetes already has a name for — so the container
args are the CLI's, unchanged (`run --tenant acme`), and nothing in the runtime
learns what a cluster is.

```bash
make -C services/seo-agents pullable        # can an anonymous client pull the image?
helm install seo-os ./helm-charts/seo-os
kubectl logs -f job/seo-os-acme-r1
```

The first line is not ceremony. GHCR makes a package private on its first push
regardless of the repository's visibility, and the symptom is a pod in
`ImagePullBackOff` with a 403 — a long way from the setting that caused it. Make
the package public, or keep it private and hand the cluster a credential:

```bash
kubectl create secret docker-registry ghcr --docker-server=ghcr.io \
  --docker-username=<you> --docker-password=$GITHUB_TOKEN
helm install seo-os ./helm-charts/seo-os --set imagePullSecrets[0].name=ghcr
```

The rest works on any cluster, and needs no credentials, no storage class and no
ingress: the default tenant names no provider, so the runtime's own defaults
apply — a mock LLM, mock analytics, in-memory state, and a single `json` output
sink writing the result to **stdout**. The run *is* the log. It proves the
plumbing before a key is involved.

What the chart renders is two objects and nothing else:

| | |
|---|---|
| `Secret` | the tenant folder — `tenant.json`, `input.json`, anything else it needs |
| `Job` | the run: the image, the args, the mounts, the limits |

No Deployment, no Service, no Ingress, no HPA. There is nothing long-running to
deploy yet — the gateway is [step 2](../../docs/roadmap.md#2-the-gateway-the-api),
and it is the thing that will bring a Deployment with it.

## Your own tenant

A tenant is a folder, and the file that matters most in it holds literal API
keys: the runtime does no `${VAR}` expansion, so `llm_options.api_key` **is** the
key and the whole file is the credential. It travels as a Secret.

The way to ship a real tenant without its keys ever entering a values file or
git is `--set-file`, straight from the folder you already run locally:

```bash
helm upgrade --install seo-os ./helm-charts/seo-os \
  --set tenant.name=echooers \
  --set-file tenant.configJson=services/seo-agents/userdata/echooers/tenant.json \
  --set-file tenant.inputJson=services/seo-agents/userdata/echooers/input.json \
  --set-file 'tenant.files.data/service_account\.json=services/seo-agents/userdata/echooers/data/service_account.json' \
  --set-file 'tenant.files.data/report\.json=services/seo-agents/userdata/echooers/data/report.json'
```

`tenant.files` takes any `relative/path: contents` pair. A Secret key can't
contain a slash, so the chart stores `data/report.json` under
`data__report.json` and projects it back to its real path with a volume item —
the pod sees an ordinary `/userdata/echooers/data/report.json`.

Two things that follow from doing it this way, both worth knowing before you do:

- **`helm get values` shows what you passed**, keys included. So does the release
  Secret in the cluster. If that is not acceptable, manage the Secret yourself
  (`tenant.secret.existing`, below) and let the chart mount it.
- **A Secret is capped at 1 MiB.** `tenant.json`, `input.json`, a service account
  and a small fixture fit comfortably. A directory of real `data/` fixtures does
  not belong in etcd — give it a volume instead.

### A Secret you already manage

`external-secrets`, `sops`, or plain `kubectl create secret generic`: the chart
renders nothing and mounts what's there.

```yaml
tenant:
  name: echooers
  secret:
    existing: echooers-tenant
    # Only needed if a key must land in a subdirectory — a Secret key can't
    # contain a slash, so the mapping has to be spelled out.
    items:
      - key: tenant.json
        path: tenant.json
      - key: input.json
        path: input.json
      - key: service_account.json
        path: data/service_account.json
```

## Where the tenant folder comes from

The Secret is one of three mechanisms, and they compose. Which you want depends
on how big the folder is and whether it contains code.

### 1. The Secret is the whole folder — the default

`tenant.secret.mount: directory` mounts it at `/userdata/<name>` and that is the
tenant. Nothing else is needed and no storage exists. Right for any tenant that
is config, an input, and a credential file or two.

### 2. A shared volume at `/userdata`

The literal translation of the Compose bind mount: one volume, every tenant, the
same layout as a laptop, and adding a tenant stays `mkdir`.

```yaml
workspace:
  volume:
    persistentVolumeClaim:
      claimName: seo-os-userdata     # ReadWriteMany
tenant:
  name: echooers
  secret:
    enabled: false                   # the folder already has its tenant.json
```

Its cost is exactly its requirement: RWX means NFS, EFS, Filestore or Azure
Files. Plenty of clusters don't have one, which is why it isn't the default.

You can also keep the volume for bulk content and still deliver the credential
file as a Secret — set `tenant.secret.mount: files` and each file lands as its
own `subPath` mount *over* the folder the volume provides:

```yaml
workspace:
  volume:
    persistentVolumeClaim:
      claimName: seo-os-userdata
tenant:
  secret:
    enabled: true
    mount: files
```

A `subPath` mount never receives updates. That is irrelevant to a Job — it reads
once and exits — and a trap for anything long-lived, so remember it when the
gateway arrives.

### 3. An init container materializing the folder

Needs no special storage class and works on every cluster including a `kind` on
a laptop. An init container pulls the tenant workspace of record into the
`emptyDir` both containers mount, and the runtime container then sees an
ordinary `/userdata/<name>`.

```yaml
initContainers:
  - name: fetch-tenant
    image: amazon/aws-cli:2
    args: ["s3", "sync", "s3://acme-tenants/echooers/", "/userdata/echooers/"]
    envFrom:
      - secretRef:
          name: tenant-sync-aws       # the fetch credentials live here, not in
                                      # the container that runs tenant code
    volumeMounts:
      - name: userdata
        mountPath: /userdata

tenant:
  secret:
    enabled: true
    mount: files                      # tenant.json still arrives as a Secret,
                                      # landing over the synced copy
```

Git works the same way — `alpine/git clone --depth 1 --branch <ref>` into
`/userdata/<name>`. Three properties make this worth an extra container: a run
gets an **immutable snapshot** of the tenant taken at start, so an edit landing
mid-run can't change what a run is doing halfway through; the pod is genuinely
stateless; and the fetch credentials live in the init container rather than in
the one executing tenant code.

The cost is real: one more image to keep, and editing a tenant becomes an upload
rather than a file save.

## Plugins are code, and code belongs in an image

This is the boundary to be strictest about, because it is where a convenience
becomes a supply-chain hole. `plugins/` is Python the runtime **imports and
executes** in a pod that also holds that tenant's API keys — so "sync the folder
from a bucket" means *anyone who can write that bucket can execute in your
cluster*. Two supported answers, split by dependencies:

**No extra dependencies** — sync the plugins with the rest of the folder
(mechanism 3), on the explicit understanding that the bucket is an artifact store
with the same access control as your image registry, not a shared drive.

**Extra dependencies** — a per-tenant image. There is no per-tenant environment
management in the runtime and never has been; the cluster is where that stops
being a nuisance and starts being the right shape, because an image is versioned,
scanned, signed and rolled back, and a bucket prefix is none of those.

```dockerfile
FROM ghcr.io/badrmada/seo-os/seo-agents:sha-abc1234
COPY requirements.extra.txt .
RUN pip install --no-cache-dir -r requirements.extra.txt
COPY plugins/ /userdata/echooers/plugins/
```

```yaml
image:
  repository: ghcr.io/acme/seo-agents-echooers
  tag: 2026-08-07
workspace:
  enabled: false        # mount nothing at /userdata — the image is the workspace
tenant:
  name: echooers
  secret:
    mount: files        # tenant.json lands *beside* the baked-in plugins
```

Both settings are load-bearing, and getting either wrong fails in a way that
looks like the plugins were never there:

- **`workspace.enabled: false`.** Any volume mounted at `/userdata` hides what
  the image put there — including `plugins/`. With it off there is no workspace
  volume at all, and the image's own folder is the tenant's.
- **`tenant.secret.mount: files`.** The default `directory` mount would replace
  `/userdata/echooers` wholesale, plugins included. `files` mounts `tenant.json`
  and `input.json` one `subPath` at a time, leaving everything else the image
  shipped in place.

## Where the result goes

A Job's filesystem dies with it. An `output_sinks` entry writing a file into the
`emptyDir` is a result nobody will ever read — the single most likely way for a
first cluster run to be quietly disappointing.

The chart's default avoids it by not writing files at all: the runtime's default
`json` sink writes to **stdout**, so `kubectl logs` is the delivery mechanism.
That is fine for a run you are watching, and not fine for one you aren't. The
two that make sense in a cluster:

```jsonc
{
  // Post the finished result somewhere that outlives the pod.
  "output_sinks": [
    {"name": "stdout", "provider": "json"},
    {"name": "gateway", "provider": "webhook",
     "options": {"url": "http://seo-os-gateway:8000/runs"}}
  ],
  // And/or: the live snapshot, readable while the run is still going.
  "state_provider": "redis",
  "state_options": {"url": "redis://redis-master.default.svc:6379/0"}
}
```

Both are tenant config, not chart config — the chart's job is to make them
reachable, which is the same relationship Compose has to them.

**`localhost` is the trap here**, and it is a quiet one. A tenant developed on a
laptop or under Compose usually carries
`"state_options": {"url": "redis://localhost:6379/0"}`, and a pod's `localhost`
is the pod. The run does not fail — a state-store outage is deliberately not a
reason to discard a finished run — so what you get is a complete, correct result
plus one line on stderr per save:

```
! state_store.save  error="save: ConnectionError: … connecting to localhost:6379."
```

That is the run telling you it produced nothing anyone else can watch. The fix is
a hostname the cluster resolves — `redis://redis-master.<namespace>.svc:6379/0` —
or `"state_provider": "memory"` to say out loud that this run isn't meant to be
watched. It has to be edited in `tenant.json`: config values are literal, so no
amount of `run.env` will reach it.

## Running it again

A Job's spec is immutable, so the release revision is part of its name
(`seo-os-acme-r1`, `-r2`, …). Every `helm upgrade --install` is therefore a new
run rather than a failed patch:

```bash
helm upgrade --install seo-os ./helm-charts/seo-os --reuse-values
kubectl logs -f job/seo-os-acme-r2
```

Helm deletes the previous revision's Job as part of the upgrade — including one
still running, so don't upgrade over a run you want. Finished Jobs disappear on
their own after `job.ttlSecondsAfterFinished` (a day, by default), because the
Job object is not where anyone should read history from.

**Scheduling** is a `CronJob` per scheduled tenant, and it isn't in the chart
yet. A cluster already has a scheduler, so using it needs nothing added to the
runtime — `concurrencyPolicy: Forbid`, because two overlapping runs of one tenant
are two drafts of the same thing at twice the API cost. One CronJob per tenant
doesn't scale to fifty; fifty is a queue, and the queue is the gateway's.

## The four Job settings that carry a decision

- **`backoffLimit: 0`.** A run spends real money on LLM calls, and the pipeline
  already degrades rather than crashing — so a pod that failed anyway failed for
  a reason a retry won't fix, and a blind retry buys a second bill and a second
  draft. A failed run is a recorded run, not a pod to run again.
- **`activeDeadlineSeconds`**, set above the runtime's own `run_timeout_seconds`.
  Two deadlines on purpose: the runtime's produces a clean recorded failure with
  its `tool_errors` intact, the Job's is the backstop for a pod that never got
  far enough to have one.
- **`ttlSecondsAfterFinished`**, because a finished Job is garbage, not a record.
- **`restartPolicy: Never`**, `runAsNonRoot`, a `readOnlyRootFilesystem` with an
  `emptyDir` for `/tmp`, dropped capabilities and real `resources.limits` — a
  tenant's own plugin code executes in this pod.

## A run without an LLM

`run.args` replaces the generated command entirely, which makes the chart a way
to run any of the CLI's other commands against a real cluster-side tenant —
useful when a config is misbehaving and you'd rather not pay for a full run:

```bash
helm upgrade --install seo-os ./helm-charts/seo-os --reuse-values \
  --set 'run.args={check-data,--tenant,echooers}'
```

`check-data` builds every configured provider, which is where a missing
credentials file or an unimportable plugin actually shows up.

## Values

Everything is documented inline in [values.yaml](values.yaml). The ones you will
actually touch:

| Value | Default | |
|---|---|---|
| `tenant.name` | `acme` | the folder name, and the `--tenant` argument |
| `tenant.config` / `tenant.configJson` | a mock-provider tenant | `tenant.json`, as YAML or verbatim |
| `tenant.input` / `tenant.inputJson` | one `site_article` request | `input.json` |
| `tenant.files` | `{}` | anything else the folder needs |
| `tenant.secret.existing` | `""` | a Secret you manage instead |
| `tenant.secret.mount` | `directory` | or `files`, to land over a volume |
| `workspace.volume` | emptyDir | what backs `/userdata` |
| `workspace.enabled` | `true` | `false` leaves the image's own `/userdata` alone |
| `run.verbosity` | `1` | `-v`; `2` for `-vv`, `0` for silence |
| `image.tag` | chart `appVersion` | pin `sha-<commit>` |
| `job.*` | see above | the four settings that carry a decision |
| `initContainers`, `extraVolumes`, `extraVolumeMounts` | `[]` | the escape hatches, so a custom shape needs no fork |
