# Local development, Docker Compose and API reference

Run the two applications without Kubernetes, call their APIs, and see how errors are reported. For the full platform (Kubernetes, monitoring, alerting, incident enrichment and autoscaling), see the [main README](../README.md#quick-start-kind).

## Run locally without Docker (macOS)

Requires Python 3.10+ (`python3 --version`).

> Code blocks in this guide contain only commands, no `#` comments, so they paste cleanly into zsh (the macOS default shell), which doesn't treat `#` as a comment at an interactive prompt.

Create an isolated Python environment, activate it (your prompt then shows `(.venv)`), and install the pinned app and test dependencies:

```bash
git clone https://github.com/rithikkampa7-pixel/cloudops-bridge.git
cd cloudops-bridge
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements-dev.txt
```

Use two terminal tabs, and activate the venv in each one (`source .venv/bin/activate`).

Tab 1, Ticket API on port 8080:

```bash
uvicorn ticket_service.main:app --port 8080 --reload
```

Tab 2, CloudOps Bridge on port 8081:

```bash
uvicorn bridge.main:app --port 8081 --reload
```

Interactive API docs: http://localhost:8080/docs and http://localhost:8081/docs

## Run with Docker Compose

**Prerequisites:** [Docker Desktop for Mac](https://www.docker.com/products/docker-desktop/) running (`docker version` shows a Server section). Ports 8080 and 8081 must be free. Stop the local uvicorn servers first if they're running.

Build both images and start them in the background, then check status. Both should show `(healthy)` after about 5–10 seconds:

```bash
docker compose up --build -d
docker compose ps
```

Every curl request in [Example requests](#example-requests) works unchanged against the containers. The images are tagged `:phase2` under Compose and `:phase3` on kind; both tags are historical names, not versions.

| What | How it's done |
|---|---|
| Base image | `python:3.13.16-slim-trixie` (pinned, never `latest`) |
| Non-root | Runs as `app` (UID 10001). App code is owned by root, so the process can't modify it |
| Health checks | Docker `HEALTHCHECK` calls `/health` using Python's standard library (no curl in the image) |
| Config | Bridge reads `CATALOG_DIR=/etc/cloudops-bridge/service-catalog` and `RUNBOOK_DIR=/etc/cloudops-bridge/runbooks`, bind-mounted **read-only** from the repo. Edit the YAML, then `docker compose restart bridge`, with no rebuild |
| Shutdown | uvicorn runs as PID 1 (exec-form `CMD`), so `docker compose down` triggers a graceful shutdown |
| Image contents | Runtime dependencies only (`requirements.txt`). No tests, pytest, or dev tools |

The catalog is deliberately **not** baked into the bridge image. Without the mount, the bridge refuses to start (`Catalog directory not found`). That's the same fail-fast behavior as a broken YAML file.

**Logs.** `-f` follows the log live; press Ctrl+C to stop following:

```bash
docker compose logs ticket-service
docker compose logs -f bridge
```

`127.0.0.1` entries are Docker's health check running inside the container. Requests from your Mac arrive from Docker Desktop's gateway (`192.168.65.1`).

**Verify non-root.** Expect `uid=10001(app)` from both containers, and `Uid: 10001 ...` for PID 1, the uvicorn process itself:

```bash
docker compose exec ticket-service id
docker compose exec bridge id
docker compose exec bridge grep Uid /proc/1/status
```

**Verify config is read-only.** This command is *expected to fail* with `Read-only file system`, and the inspect should print `RW=false` twice:

```bash
docker compose exec bridge touch /etc/cloudops-bridge/service-catalog/test
docker inspect cloudops-bridge-bridge-1 --format '{{range .Mounts}}{{.Destination}} RW={{.RW}}{{"\n"}}{{end}}'
```

**Stop** and remove the containers and network:

```bash
docker compose down
```

Inventory is in memory, so it resets to 5000 whenever the ticket-service container is recreated.

## Example requests

Ticket API:

```bash
curl -s localhost:8080/health
curl -s localhost:8080/ready
curl -s localhost:8080/tickets
curl -s -X POST localhost:8080/tickets/purchase -H 'Content-Type: application/json' -d '{"quantity": 2}'
curl -s localhost:8080/metrics
```

CloudOps Bridge:

```bash
curl -s localhost:8081/ready
curl -s localhost:8081/services
curl -s -X POST localhost:8081/incidents/enrich -H 'Content-Type: application/json' -d '{"service":"ticket-api","environment":"production","alert":"HighErrorRate"}' | python3 -m json.tool
```

Error cases. `-w` prints the HTTP status code; expect `[404]`, `[422]`, `[422]`:

```bash
curl -s -w ' [%{http_code}]\n' -X POST localhost:8081/incidents/enrich -H 'Content-Type: application/json' -d '{"service":"payments","environment":"production","alert":"HighErrorRate"}'
curl -s -w ' [%{http_code}]\n' -X POST localhost:8081/incidents/enrich -H 'Content-Type: application/json' -d '{"service":"ticket-api"}'
curl -s -w ' [%{http_code}]\n' -X POST localhost:8080/tickets/purchase -H 'Content-Type: application/json' -d '{"quantity": 0}'
```

Example enriched incident:

```json
{
  "service": "ticket-api",
  "environment": "production",
  "alert": "HighErrorRate",
  "summary": "Elevated rate of HTTP 5xx responses.",
  "severity": "critical",
  "first_responder": "cloudops",
  "application_owner": "Ticket Development",
  "cloudops_owner": "CloudOps",
  "dependencies": ["postgresql"],
  "health_endpoint": "/health",
  "readiness_endpoint": "/ready",
  "runbook": "high-error-rate.md",
  "suggested_checks": [
    "Check application health (GET /health and /ready)",
    "Review application logs for new or repeated exceptions",
    "Check recent deployments",
    "Verify database connectivity",
    "Consider rollback if deployment-related"
  ]
}
```

## Error handling

| Situation | Response |
|---|---|
| Ticket quantity < 1, > 10, or not an integer | `422` |
| More tickets requested than available | `409 Conflict` |
| Unknown service | `404` |
| Known service, unknown environment or alert | `422` |
| Missing fields or malformed JSON | `422` |
| Unexpected server error | `500 {"detail": "Internal server error"}` (stack trace logged, never returned) |

The Bridge **validates the catalog at startup** and refuses to start if a YAML file is malformed, so a broken handoff file is caught before an incident, not during one.

## Try the Alertmanager webhook with a sample payload

With the Bridge running (locally or under Compose), post a stored sample of Alertmanager's v4 payload. This is **not** a real Alertmanager delivery; the real end-to-end path is in [kubernetes/monitoring/README.md, section 11](../kubernetes/monitoring/README.md#11-incident-enrichment-alertmanager--cloudops-bridge):

```bash
curl -s -X POST localhost:8081/webhooks/alertmanager -H 'Content-Type: application/json' --data @tests/fixtures/alertmanager_target_down_firing.json | python3 -m json.tool
```
