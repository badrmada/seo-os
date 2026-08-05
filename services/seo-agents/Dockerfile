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
