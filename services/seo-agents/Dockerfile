FROM python:3.11-slim
WORKDIR /workspace
COPY requirements.txt .
RUN pip install --upgrade pip
RUN python -m venv venv
ENV PATH="/workspace/venv/bin:$PATH"
RUN pip install --no-cache-dir -r requirements.txt
COPY src ./src
WORKDIR /workspace/src
# Runs the one-shot example hardcoded in main.py (see agent/config/agent_config.py / main.py to change it).
ENTRYPOINT ["python", "main.py"]
