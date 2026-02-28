FROM python:3.12-slim

LABEL maintainer="VAPI Project"
LABEL description="VAPI Bridge — PoAC Record Relay Service"

WORKDIR /app

# Install dependencies first (layer caching)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY vapi_bridge/ vapi_bridge/

# Create data directory for SQLite
RUN mkdir -p /data

ENV DB_PATH=/data/bridge.db
ENV HTTP_HOST=0.0.0.0
ENV HTTP_PORT=8080
ENV LOG_LEVEL=INFO

EXPOSE 8080
EXPOSE 5683/udp

HEALTHCHECK --interval=30s --timeout=5s \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8080/api/v1/stats')" || exit 1

ENTRYPOINT ["python", "-m", "vapi_bridge.main"]
