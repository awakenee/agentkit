FROM python:3.11-slim

# Unbuffered logs (so platform log viewers see output immediately) and no .pyc.
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    AGENT_MEMORY_DIR=/data/memory \
    AGENT_WORKDIR=/data/workspace \
    PORT=8080

WORKDIR /app

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY *.py ./

# Run as a non-root user; /data must stay writable for the memory volume.
RUN useradd --create-home --uid 1000 agent \
    && mkdir -p /data/memory /data/workspace \
    && chown -R agent:agent /data /app
USER agent

VOLUME ["/data"]
EXPOSE 8080

# `--check` runs first so a bad key shows up as a clear log line, not a crash loop.
CMD ["sh", "-c", "python bot.py --check; exec python bot.py"]

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD python -c "import os,urllib.request;urllib.request.urlopen(f\"http://127.0.0.1:{os.environ.get('PORT','8080')}/health\",timeout=4)"
