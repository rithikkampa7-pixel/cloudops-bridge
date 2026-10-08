# CloudOps Bridge image.
# Build from the repo root:  docker build -f bridge/Dockerfile .
#
# The service catalog and runbooks are NOT baked into the image. They are
# operational configuration supplied at runtime (read-only bind mounts in
# compose.yaml), so they can change without rebuilding. Without them the
# bridge refuses to start, by design.

FROM python:3.13.16-slim-trixie

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    CATALOG_DIR=/etc/cloudops-bridge/service-catalog \
    RUNBOOK_DIR=/etc/cloudops-bridge/runbooks

WORKDIR /app

COPY requirements.txt .
RUN pip install -r requirements.txt

RUN useradd --system --uid 10001 --no-create-home --shell /usr/sbin/nologin app

COPY bridge/ ./bridge/

USER app

EXPOSE 8081

HEALTHCHECK --interval=10s --timeout=3s --start-period=5s --retries=3 \
    CMD ["python", "-c", "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8081/health', timeout=2).status == 200 else 1)"]

CMD ["uvicorn", "bridge.main:app", "--host", "0.0.0.0", "--port", "8081"]
