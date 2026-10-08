# Demo Ticket Service image.
# Build from the repo root:  docker build -f ticket_service/Dockerfile .

FROM python:3.13.16-slim-trixie

# Unbuffered output so logs appear in `docker logs` immediately; no .pyc
# files; no pip cache or version-check noise in the image.
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# Dependencies first: this layer is reused until requirements.txt changes.
COPY requirements.txt .
RUN pip install -r requirements.txt

# Unprivileged user with a fixed UID. Application files stay owned by root,
# so the running process can read but not modify its own code.
RUN useradd --system --uid 10001 --no-create-home --shell /usr/sbin/nologin app

COPY ticket_service/ ./ticket_service/

USER app

EXPOSE 8080

# slim has no curl; use Python's standard library instead of installing one.
HEALTHCHECK --interval=10s --timeout=3s --start-period=5s --retries=3 \
    CMD ["python", "-c", "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8080/health', timeout=2).status == 200 else 1)"]

# Exec form: uvicorn is PID 1 and receives SIGTERM directly for graceful shutdown.
CMD ["uvicorn", "ticket_service.main:app", "--host", "0.0.0.0", "--port", "8080"]
