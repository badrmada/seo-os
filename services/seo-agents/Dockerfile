FROM python:3.11-slim
WORKDIR /workspace
COPY requirements.txt .
RUN pip install --upgrade pip
RUN python -m venv venv
ENV PATH="/workspace/venv/bin:$PATH"
RUN pip install --no-cache-dir -r requirements.txt
COPY src ./src
WORKDIR /workspace/src

# Tenants are mounted, not baked in: one image serves every tenant whose folder
# appears under /userdata.
#
#   docker run -v ./userdata:/userdata <image> run --tenant acme
#   docker run -v ./userdata:/userdata <image> list-tenants
#   docker run <image> --help
#
# A tenant whose plugins need packages this image doesn't ship needs its own
# image: add them to requirements.txt and rebuild. There is no per-tenant
# environment management — see docs/extending.md.
ENV SEO_AGENT_USERDATA=/userdata
VOLUME ["/userdata"]

# Every command is explicit; a bare `docker run <image>` prints the help.
ENTRYPOINT ["python", "main.py"]

# OCI image annotations (opencontainers.org/spec) — the standard `docker
# inspect`, GHCR's package page, Renovate and every scanner already read. Not the
# deprecated `org.label-schema.*` this replaced.
#
# The split is deliberate. The three build-time facts arrive as ARGs, declared
# here at the end so a new commit doesn't invalidate the pip-install layer above;
# everything else is a property of the project rather than of the build, so it
# lives in the file instead of in whatever command happened to build the image.
#
# `make build` supplies the ARGs from git (see Makefile). A bare `docker build`
# still works and simply labels the image `dev` — the fallbacks below are what
# make that true. In CI, docker/metadata-action passes the same annotations as
# `--label` flags, which take precedence: one image shape from either path.
ARG VERSION=dev
ARG REVISION=unknown
ARG CREATED=unknown

LABEL org.opencontainers.image.title="seo-agents" \
      org.opencontainers.image.description="SEO-OS runtime: a configurable, tool-using SEO content agent. Tenants are mounted at /userdata, never baked in." \
      org.opencontainers.image.url="https://github.com/badrmada/seo-os" \
      org.opencontainers.image.source="https://github.com/badrmada/seo-os" \
      org.opencontainers.image.documentation="https://github.com/badrmada/seo-os/blob/main/services/seo-agents/README.md" \
      org.opencontainers.image.licenses="MIT" \
      org.opencontainers.image.version="${VERSION}" \
      org.opencontainers.image.revision="${REVISION}" \
      org.opencontainers.image.created="${CREATED}"
