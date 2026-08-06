# The commands CI runs, in the place a person can run them too.
#
# That is the whole point: `tests.yml` and `images.yml` call the same targets you
# do, so "works on my laptop, fails in CI" stops being a category of problem. A
# workflow file is a bad place to read a command out of.
#
#   make                      # this help
#   make test                 # the suite
#   make build                # the image, labelled from git
#   make publish              # login, build, push, and check it can be pulled
#   make example EXAMPLE=02-saas-blog-pingowl
#   make example EXAMPLE=02-saas-blog-pingowl ENGINE=docker
#   make run TENANT=acme
#
# Every variable below is `?=`, so any of them can be overridden per invocation:
#   make build TAG=v0.2.0-rc1
#   make publish IMAGE_REPO=registry.example.com/seo-os
#   make example EXAMPLE=04-community-homelabhub ARGS="--input input.discover.json"

# ---------------------------------------------------------------------------
# Where the image goes. Matches deploy/compose/.env.example, which defaults to
# the same GHCR namespace images.yml publishes to.
#
# IMAGE_REPO is the one variable to override for a registry of your own — a
# private Harbor, a registry on the host that runs the cluster — and everything
# below follows from it, including which registry `login` talks to:
#
#   make publish IMAGE_REPO=registry.example.com/seo-os
# ---------------------------------------------------------------------------
IMAGE_REPO ?= ghcr.io/badrmada/seo-os
IMAGE_NAME ?= seo-agents
IMAGE      ?= $(IMAGE_REPO)/$(IMAGE_NAME)
REGISTRY   ?= $(firstword $(subst /, ,$(IMAGE_REPO)))
REPO_PATH  ?= $(patsubst $(REGISTRY)/%,%,$(IMAGE))

# Who pushes, and with what. The token is read from the environment and passed
# on stdin, never on a command line where `ps` would show it — so a PAT with
# `write:packages` in your shell is all `make login` needs.
REGISTRY_USER  ?= $(word 2,$(subst /, ,$(IMAGE_REPO)))
REGISTRY_TOKEN ?= $(or $(GITHUB_TOKEN),$(GH_TOKEN),$(CR_PAT))
export REGISTRY_TOKEN

# ---------------------------------------------------------------------------
# Image metadata, from git rather than from a hand-maintained constant.
#
# `git describe --tags --match 'v*'` is the standard derivation: an exact tag
# gives `v1.2.0`, a commit past one gives `v1.2.0-3-gabc1234`, and `--dirty`
# marks a build made from uncommitted work — which is a thing you want on the
# label of an image you are about to push somewhere. With no tags in the repo
# yet it falls back to a short SHA, so this works today and starts producing
# SemVer the moment `v0.1.0` exists. `|| echo dev` covers a tarball with no
# git at all.
# ---------------------------------------------------------------------------
VERSION  ?= $(shell git describe --tags --always --dirty --match 'v*' 2>/dev/null || echo dev)
REVISION ?= $(shell git rev-parse HEAD 2>/dev/null || echo unknown)
CREATED  ?= $(shell date -u +%Y-%m-%dT%H:%M:%SZ)
TAG      ?= $(VERSION)

# `build` tags the image twice — the version and `latest` — because `latest` is
# what deploy/compose/.env.example and the Helm chart both default to. `push`
# therefore has to send both, or a hand-built image lands in the registry under a
# version nothing is configured to pull while `latest` silently stays whatever
# CI last published. PUSH_LATEST=0 opts out, which is what you want when
# publishing an old tag or a release candidate.
PUSH_LATEST ?= 1

# Empty means "push the architecture you just built", which is the honest
# default for a laptop: `docker push` can only send what `docker build` made.
# Set it to cross-build and push in one step, the way images.yml does —
#
#   make push PLATFORMS=linux/amd64,linux/arm64
#
# — which needs a buildx builder that isn't the default docker driver:
#
#   docker buildx create --name seo-os --driver docker-container --use
#
# It matters when the machine you build on and the machine you deploy to don't
# agree: an arm64 image on an amd64 node is an `exec format error` at runtime,
# not a pull failure, which makes it a slow thing to diagnose.
PLATFORMS ?=

# ---------------------------------------------------------------------------
# How a run happens. ENGINE=python uses the local virtualenv; ENGINE=docker uses
# the image, mounting the same folder the venv would have read. Same command,
# one variable — which is what makes them comparable.
# ---------------------------------------------------------------------------
ENGINE   ?= python
EXAMPLE  ?= 01-starter-acme
TENANT   ?=
USERDATA ?= $(CURDIR)/userdata
ARGS     ?=

# vir/ first, .venv/ second — the local convention, and the same order
# check_docs.py looks in. Falls back to whatever `python3` is on PATH.
PYTHON ?= $(firstword $(wildcard vir/bin/python .venv/bin/python) python3)

DOCKER ?= docker

# ENGINE=docker on an image that isn't here locally would otherwise fall through
# to an implicit `docker pull` and fail with the registry's error — which is a
# 403 while the GHCR package is private, and says nothing about what to do. Ask
# first, and name both fixes.
define require_image
@$(DOCKER) image inspect $(IMAGE):$(TAG) >/dev/null 2>&1 || { \
	echo "no local image $(IMAGE):$(TAG)"; \
	echo "  build it:  make build"; \
	echo "  or pull:   docker pull $(IMAGE):$(TAG)   (see: make pullable)"; \
	exit 2; }
endef

.DEFAULT_GOAL := help
.PHONY: help test build login push publish pullable version labels example run

help: ## Show this help
	@echo "SEO-OS runtime — services/seo-agents"
	@echo
	@grep -E '^[a-z-]+:.*?## ' $(MAKEFILE_LIST) \
		| awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-10s\033[0m %s\n", $$1, $$2}'
	@echo
	@echo "  version  $(VERSION)"
	@echo "  image    $(IMAGE):$(TAG)"

test: ## Run the test suite (what tests.yml runs)
	$(PYTHON) -m pytest -q

build: ## Build the image, labelled from git
	$(DOCKER) build \
		--build-arg VERSION=$(VERSION) \
		--build-arg REVISION=$(REVISION) \
		--build-arg CREATED=$(CREATED) \
		-t $(IMAGE):$(TAG) \
		-t $(IMAGE):latest \
		.

login: ## Log in to the registry (reads GITHUB_TOKEN / GH_TOKEN / CR_PAT)
	@test -n "$$REGISTRY_TOKEN" || { \
		echo "no registry token in the environment"; \
		echo "  GHCR wants a PAT with write:packages (read:packages to pull):"; \
		echo "    export GITHUB_TOKEN=ghp_...   # or GH_TOKEN, or CR_PAT"; \
		echo "  another registry: docker login $(REGISTRY) yourself and skip this"; \
		exit 2; }
	@printf '%s' "$$REGISTRY_TOKEN" | $(DOCKER) login $(REGISTRY) -u $(REGISTRY_USER) --password-stdin

push: ## Push the version tag and latest (PLATFORMS=... cross-builds)
ifeq ($(PLATFORMS),)
	$(require_image)
	$(DOCKER) push $(IMAGE):$(TAG)
ifeq ($(PUSH_LATEST),1)
	@$(DOCKER) image inspect $(IMAGE):latest >/dev/null 2>&1 \
		|| $(DOCKER) tag $(IMAGE):$(TAG) $(IMAGE):latest
	$(DOCKER) push $(IMAGE):latest
endif
else
	$(DOCKER) buildx build --platform $(PLATFORMS) --push \
		--build-arg VERSION=$(VERSION) \
		--build-arg REVISION=$(REVISION) \
		--build-arg CREATED=$(CREATED) \
		-t $(IMAGE):$(TAG) \
		$(if $(filter 1,$(PUSH_LATEST)),-t $(IMAGE):latest,) \
		.
endif

publish: build push pullable ## build + push + prove the result is pullable

# The check that would have saved an afternoon: a `helm install` whose pod sits
# in ImagePullBackOff because GHCR made the package private on its first push —
# which it does regardless of the repository's visibility, and which nothing in
# a successful `docker push` tells you, because the machine that pushed is
# authenticated and cannot notice.
#
# So this asks the registry the question a cluster asks: can an *anonymous*
# client pull this? GHCR's token endpoint answers it without pulling a byte.
# Other registries are legitimately private on purpose; there the answer is an
# imagePullSecret, and the target says so instead of failing.
pullable: ## Check an anonymous client can pull it — what a cluster does
ifeq ($(REGISTRY),ghcr.io)
	@token=$$(curl -sf "https://ghcr.io/token?scope=repository:$(REPO_PATH):pull&service=ghcr.io" \
		| sed -n 's/.*"token":"\([^"]*\)".*/\1/p'); \
	code=$$(curl -s -o /dev/null -w '%{http_code}' -H "Authorization: Bearer $$token" \
		-H 'Accept: application/vnd.oci.image.index.v1+json, application/vnd.docker.distribution.manifest.list.v2+json, application/vnd.docker.distribution.manifest.v2+json' \
		"https://ghcr.io/v2/$(REPO_PATH)/manifests/$(TAG)"); \
	if [ "$$code" = "200" ]; then \
		echo "ok  $(IMAGE):$(TAG) is publicly pullable"; \
	else \
		echo "not pullable anonymously ($$code) — $(IMAGE):$(TAG)"; \
		echo "  a cluster pulling this gets ImagePullBackOff / 403."; \
		echo "  make it public:  github.com/badrmada?tab=packages -> $(IMAGE_NAME)"; \
		echo "                   -> Package settings -> Change visibility -> Public"; \
		echo "  or keep it private and give the cluster a credential:"; \
		echo "    kubectl create secret docker-registry ghcr --docker-server=ghcr.io \\"; \
		echo "      --docker-username=$(REGISTRY_USER) --docker-password=\$$GITHUB_TOKEN"; \
		echo "    helm upgrade ... --set imagePullSecrets[0].name=ghcr"; \
		exit 1; \
	fi
else
	@echo "skip  anonymous-pull check is GHCR-specific; $(REGISTRY) may be private on purpose"
	@echo "      a private registry needs an imagePullSecret in the cluster either way"
endif

version: ## Print the version this build would carry
	@echo "version  $(VERSION)"
	@echo "revision $(REVISION)"
	@echo "created  $(CREATED)"
	@echo "image    $(IMAGE):$(TAG)"

labels: ## Show the OCI labels on the built image
	@$(DOCKER) inspect --format '{{range $$k, $$v := .Config.Labels}}{{$$k}}={{$$v}}{{"\n"}}{{end}}' $(IMAGE):$(TAG)

# `--userdata examples` for the venv; for the image it is the mount itself,
# because the image already sets SEO_AGENT_USERDATA=/userdata — so the docker
# form needs no `--userdata` flag and the two commands stay this close.
example: ## Run an example: make example EXAMPLE=02-saas-blog-pingowl [ENGINE=docker]
ifeq ($(ENGINE),docker)
	$(require_image)
	$(DOCKER) run --rm -v "$(CURDIR)/examples:/userdata" $(IMAGE):$(TAG) \
		run --tenant $(EXAMPLE) $(ARGS)
else
	$(PYTHON) src/main.py run --userdata examples --tenant $(EXAMPLE) $(ARGS)
endif

run: ## Run a real tenant: make run TENANT=acme [ENGINE=docker]
	@test -n "$(TENANT)" || { echo "TENANT is required, e.g. make run TENANT=acme"; exit 2; }
ifeq ($(ENGINE),docker)
	$(require_image)
	$(DOCKER) run --rm -v "$(USERDATA):/userdata" $(IMAGE):$(TAG) \
		run --tenant $(TENANT) $(ARGS)
else
	$(PYTHON) src/main.py run --tenant $(TENANT) $(ARGS)
endif
