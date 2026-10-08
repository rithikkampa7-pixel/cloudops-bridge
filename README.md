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

## Current status: Phase 1

| Component | Status |
|---|---|
| Demo Ticket Service (FastAPI) | ✅ Implemented |
| CloudOps Bridge enrichment API (FastAPI) | ✅ Implemented |
| YAML service catalog | ✅ Implemented |
| Markdown runbook (`HighErrorRate`) | ✅ Implemented |
| `/metrics` in Prometheus text format | ✅ Implemented (no Prometheus server yet) |
| Automated tests (pytest) | ✅ Implemented |
| Docker, Kubernetes (kind), HPA autoscaling | 🔜 Future work |
| Prometheus, Alertmanager, Grafana | 🔜 Future work |
| PostgreSQL, deployment tracking, incident timeline | 🔜 Future work |
| Locust load testing (ticket on-sale spike) | 🔜 Future work |
| Argo CD, GitHub Actions, Terraform, Ansible | 🔜 Future work |
| Slack-style notifications, dashboard, incident reports | 🔜 Future work |

## Phase 1 architecture

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

The two services **do not call each other** in Phase 1. The Ticket API is the system being monitored. The Bridge is the incident-context tool. In a later phase, Prometheus will scrape the Ticket API's `/metrics` and Alertmanager will send alerts to the Bridge automatically.

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
├── requirements.txt
└── pytest.ini
```

## Setup (macOS)

Requires Python 3.10+ (`python3 --version`).

```bash
cd cloudops-bridge
python3 -m venv .venv               # create an isolated Python environment
source .venv/bin/activate           # activate it (prompt shows "(.venv)")
pip install -r requirements.txt     # install pinned dependencies
```

## Run

Use two terminal tabs, and activate the venv in each one (`source .venv/bin/activate`).

```bash
# Tab 1: Ticket API on port 8080
uvicorn ticket_service.main:app --port 8080 --reload

# Tab 2: CloudOps Bridge on port 8081
uvicorn bridge.main:app --port 8081 --reload
```

Interactive API docs: http://localhost:8080/docs and http://localhost:8081/docs

## Example requests

```bash
# Ticket API
curl -s localhost:8080/health
curl -s localhost:8080/ready
curl -s localhost:8080/tickets
curl -s -X POST localhost:8080/tickets/purchase \
  -H 'Content-Type: application/json' -d '{"quantity": 2}'
curl -s localhost:8080/metrics

# CloudOps Bridge
curl -s -X POST localhost:8081/incidents/enrich \
  -H 'Content-Type: application/json' \
  -d '{"service":"ticket-api","environment":"production","alert":"HighErrorRate"}' \
  | python3 -m json.tool
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

```bash
pytest -v
```

Covers health, readiness, tickets, valid and invalid purchases, the sold-out case, the metrics format, enrichment, environment-based severity, the missing-runbook case, unknown service, unknown environment or alert, missing or invalid fields, malformed JSON, and catalog integrity.
