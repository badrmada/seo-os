FROM python:3.11-slim
WORKDIR /workspace
COPY requirements.txt .
RUN pip install --upgrade pip
RUN python -m venv venv
ENV PATH="/workspace/venv/bin:$PATH"
RUN pip install --no-cache-dir -r requirements.txt
COPY src ./src
WORKDIR /workspace/src
# Every command is explicit: `docker run <image> run --tenant t.json --input i.json`
# runs the agent; `docker run <image> check-data` / `show-graph` / `--help` also work,
# and a bare `docker run <image>` prints the help. See docs/cli.md.
ENTRYPOINT ["python", "main.py"]
