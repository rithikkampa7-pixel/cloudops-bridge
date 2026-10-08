# CloudOps Bridge

**Proactive Incident Context & Operational Readiness Platform**

CloudOps Bridge takes a bare alert like *"HighErrorRate on ticket-api in production"* and turns it into an **enriched incident**. The incident says who owns the service, what it depends on, how severe the alert is, which runbook to open, and what to check first.

> This is a fictional demonstration project. All services, teams, and data are made up.

## The problem

On-call engineers often get paged with very little context. They lose the first minutes of an incident trying to answer:

- What service is failing, and in which environment?
- Who owns it: Development or CloudOps?
- What does it depend on?
- Is there a runbook? Where is it?
- What should I check first?

CloudOps Bridge answers these from a **service catalog**: one YAML file per service that the Development and CloudOps teams agree on as their operational handoff. The knowledge lives in version control instead of in someone's head.

## Current status: Phase 2 complete

| Component | Status |
|---|---|
| Demo Ticket Service (FastAPI) | ✅ Implemented |
| CloudOps Bridge enrichment API (FastAPI) | ✅ Implemented |
| YAML service catalog | ✅ Implemented |
| Markdown runbook (`HighErrorRate`) | ✅ Implemented |
| `/metrics` in Prometheus text format | ✅ Implemented (no Prometheus server yet) |
| Automated tests (pytest) | ✅ Implemented |
| **Docker images + Docker Compose** (non-root, health checks, read-only config mounts) | ✅ Implemented (Phase 2) |
| Kubernetes (kind), HPA autoscaling | 🔜 Future work |
| Prometheus, Alertmanager, Grafana | 🔜 Future work |
| PostgreSQL, deployment tracking, incident timeline | 🔜 Future work |
| Locust load testing (ticket on-sale spike) | 🔜 Future work |
| Argo CD, GitHub Actions, Terraform, Ansible | 🔜 Future work |
| Slack-style notifications, dashboard, incident reports | 🔜 Future work |

## Architecture (Phases 1–2)

```
                    (you, with curl, acting as Alertmanager)
                                    |
                                    | POST /incidents/enrich
                                    | {service, environment, alert}
                                    v
+---------------------+     +-------------------+     service-catalog/*.yaml
| Demo Ticket Service |     |  CloudOps Bridge  | <-- (owners, dependencies,
|  :8080              |     |  :8081            |      alerts, severities,
|  /health  /ready    |     +-------------------+      runbook mappings)
|  /tickets /metrics  |               |
|  /tickets/purchase  |               v
+---------------------+       Enriched incident (JSON)
```

In Phase 2 each box runs in its own container (see [Run with Docker](#run-with-docker-phase-2)); the design is unchanged.

The two services **do not call each other** yet. The Ticket API is the system being monitored. The Bridge is the incident-context tool. In a later phase, Prometheus will scrape the Ticket API's `/metrics` and Alertmanager will send alerts to the Bridge automatically.

## Project layout

```
cloudops-bridge/
├── ticket_service/          # Demo Ticket Service
│   ├── main.py              #   API routes
│   ├── inventory.py         #   in-memory, thread-safe ticket inventory
│   └── metrics.py           #   Prometheus-format request/ticket metrics
├── bridge/                  # CloudOps Bridge
│   ├── main.py              #   API routes + enrichment logic
│   ├── catalog.py           #   loads and validates service-catalog/*.yaml
│   └── models.py            #   Pydantic schemas (catalog + API)
├── service-catalog/
│   └── ticket-api.yaml      # Dev/CloudOps handoff contract
├── runbooks/
│   └── high-error-rate.md
├── tests/
├── ticket_service/Dockerfile, bridge/Dockerfile
├── compose.yaml             # starts both containers
├── .dockerignore
├── requirements.txt         # runtime deps (installed in images)
├── requirements-dev.txt     # runtime + test deps (local development)
└── pytest.ini
```

## Run locally without Docker (macOS)

Requires Python 3.10+ (`python3 --version`).

> Code blocks in this README contain only commands, no `#` comments, so they paste cleanly into zsh (the macOS default shell), which doesn't treat `#` as a comment at an interactive prompt.

Create an isolated Python environment, activate it (your prompt then shows `(.venv)`), and install the pinned app and test dependencies:

```bash
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

## Run with Docker (Phase 2)

**Prerequisites:** [Docker Desktop for Mac](https://www.docker.com/products/docker-desktop/) running (`docker version` shows a Server section). Ports 8080 and 8081 must be free. Stop the local uvicorn servers first if they're running.

Build both images and start them in the background, then check status. Both should show `(healthy)` after about 5–10 seconds:

```bash
docker compose up --build -d
docker compose ps
```

Every curl request in [Example requests](#example-requests) works unchanged against the containers.

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

## Design notes

- **Catalog-driven, not hardcoded.** To add a service, add a YAML file. No Python changes are needed.
- **Severity depends on environment.** `HighErrorRate` is `critical` in production and `warning` in staging.
- **Documentation gaps are visible.** `HighLatency` deliberately has no runbook. The Bridge returns `runbook: null` and adds a reminder to document the fix afterward.
- **A test checks that every runbook referenced in the catalog exists**, so the catalog and the docs can't drift apart.
- **The inventory lives in memory** and resets on restart. PostgreSQL arrives in a later phase.

## Tests

Tests run outside Docker, against the code directly:

```bash
source .venv/bin/activate
pip install -r requirements-dev.txt
pytest -v
```

Covers health, readiness, tickets, valid and invalid purchases, the sold-out case, the metrics format, enrichment, environment-based severity, the missing-runbook case, unknown service, unknown environment or alert, missing or invalid fields, malformed JSON, and catalog integrity.
