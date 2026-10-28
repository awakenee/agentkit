FROM python:3.11-slim

WORKDIR /app

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    AGENT_MEMORY_DIR=/data/memory \
    AGENT_WORKDIR=/data/workspace

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY . ./

VOLUME ["/data"]

CMD ["python", "bot.py"]
