# CaseLine Guava voice agent. Guava agents only make outbound connections, so this runs as a background worker
# on any host. Build context: apps/voice
FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
WORKDIR /app
COPY pyproject.toml main.py ./
COPY caseline_voice ./caseline_voice
RUN pip install . && useradd --create-home --uid 10001 caseline
USER caseline
# Needs GUAVA_API_KEY, GUAVA_AGENT_NUMBER, CASELINE_API_BASE_URL, CASELINE_INTERNAL_API_TOKEN.
CMD ["python", "main.py"]
