# The commands CI runs, in the place a person can run them too.
#
# That is the whole point: `tests.yml` and `images.yml` call the same targets you
# do, so "works on my laptop, fails in CI" stops being a category of problem. A
# workflow file is a bad place to read a command out of.
#
#   make                      # this help
#   make test                 # the suite
#   make build                # the image, labelled from git
#   make example EXAMPLE=02-saas-blog-pingowl
#   make example EXAMPLE=02-saas-blog-pingowl ENGINE=docker
#   make run TENANT=acme
#
# Every variable below is `?=`, so any of them can be overridden per invocation:
#   make build TAG=v0.2.0-rc1
#   make example EXAMPLE=04-community-homelabhub ARGS="--input input.discover.json"

# ---------------------------------------------------------------------------
# Where the image goes. Matches deploy/compose/.env.example, which defaults to
# the same GHCR namespace images.yml publishes to.
# ---------------------------------------------------------------------------
IMAGE_REPO ?= ghcr.io/badrmada/seo-os
IMAGE_NAME ?= seo-agents
IMAGE      ?= $(IMAGE_REPO)/$(IMAGE_NAME)

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
	echo "  or pull:   docker pull $(IMAGE):$(TAG)   (needs the package to be published)"; \
	exit 2; }
endef

.DEFAULT_GOAL := help
.PHONY: help test build push version labels example run

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

push: ## Push the image to the registry
	$(DOCKER) push $(IMAGE):$(TAG)

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
