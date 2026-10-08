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

## Current status: Phase 6 complete

| Component | Status |
|---|---|
| Demo Ticket Service (FastAPI) | ✅ Implemented |
| CloudOps Bridge enrichment API (FastAPI) | ✅ Implemented |
| YAML service catalog | ✅ Implemented |
| Markdown runbook (`HighErrorRate`) | ✅ Implemented |
| `/metrics` in Prometheus text format | ✅ Implemented |
| Automated tests (pytest) | ✅ Implemented |
| **Docker images + Docker Compose** (non-root, health checks, read-only config mounts) | ✅ Implemented (Phase 2) |
| **Kubernetes on kind**: Deployments, ClusterIP Services, ConfigMaps, liveness/readiness probes, requests/limits, hardened securityContext, rolling update and rollback | ✅ Implemented (Phase 3) |
| HPA autoscaling | 🔜 Future work |
| **Prometheus** with Kubernetes pod discovery, PromQL, **Grafana** with provisioned data source and *CloudOps Bridge - Ticket Service Overview* dashboard | ✅ Implemented (Phase 4) |
| **Prometheus alert rules** (`HighErrorRate` on 5xx, `TicketServiceTargetDown` on `up`) and **Alertmanager** (grouping, alert lifecycle, no notifications yet), validated with promtool/amtool | ✅ Implemented (Phase 5) |
| **Alertmanager → CloudOps Bridge webhook** (`POST /webhooks/alertmanager`): real firing and resolved alerts enriched from the service catalog, `TicketServiceTargetDown` catalog entry and runbook | ✅ Implemented (Phase 6) |
| Incident persistence/history, webhook authentication, root-cause analysis, automated remediation | 🔜 Not implemented (by design in Phase 6) |
| `HighLatency` alert (the app exports no latency metric yet); live `HighErrorRate` demo (no safe way to produce 5xx without an approved app change) | 🔜 Not possible yet |
| PostgreSQL, deployment tracking, incident timeline | 🔜 Future work |
| Locust load testing (ticket on-sale spike) | 🔜 Future work |
| Argo CD, GitHub Actions, Terraform, Ansible | 🔜 Future work |
| Slack integration, dashboard, incident reports | 🔜 Future work |

## Architecture (Phases 1–6)

```
                   ┌──────────────┐
                   │Ticket Service│  :8080  /tickets /tickets/purchase /health /ready /metrics
                   └──────┬───────┘
                          │ metrics (scraped every 15s, each pod)
                          ▼
                   ┌──────────────┐  PromQL   ┌─────────┐
                   │  Prometheus  │ ────────► │ Grafana │  dashboard (visualization)
                   └──────┬───────┘           └─────────┘
                          │ firing/resolved (rules: TicketServiceTargetDown, HighErrorRate)
                          ▼
                   ┌──────────────┐
                   │ Alertmanager │  groups, times, retries
                   └──────┬───────┘
                          │ webhook  POST /webhooks/alertmanager  (service="ticket-api")
                          ▼
                  ┌────────────────┐
                  │CloudOps Bridge │  :8081  also: POST /incidents/enrich (manual)
                  └───────┬────────┘
                          │
                 ┌────────┴────────┐
                 ▼                 ▼
          Service Catalog       Runbooks        → enriched incident: owners, first responder,
          (service-catalog/)    (runbooks/)       dependencies, endpoints, runbook, checks
```

On Kubernetes (Phases 3–6), each application runs as a 2-replica Deployment behind a ClusterIP Service. The catalog and runbooks are mounted from ConfigMaps, and monitoring runs in the `monitoring` namespace. Under Docker Compose (Phase 2), only the two applications run; you can call either API with curl, including the webhook with a sample payload.

**Responsibilities:** Prometheus *detects*, Alertmanager *delivers*, the Bridge *explains*: who owns it, who responds first, what it depends on, what to check, which runbook. The Bridge doesn't diagnose root causes or remediate.

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
│   ├── alertmanager.py      #   Alertmanager webhook schema + per-alert processing (Phase 6)
│   └── models.py            #   Pydantic schemas (catalog + API)
├── service-catalog/
│   └── ticket-api.yaml      # Dev/CloudOps handoff contract
├── runbooks/
│   ├── high-error-rate.md
│   └── ticket-service-target-down.md
├── tests/
├── ticket_service/Dockerfile, bridge/Dockerfile
├── compose.yaml             # starts both containers
├── kubernetes/              # Phase 3 manifests, ConfigMap generator, demo tools
│   └── monitoring/          # Phase 4–5 Prometheus, Grafana, alert rules, Alertmanager
├── dashboards/              # Grafana dashboard JSON (source of truth)
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

## Run on Kubernetes with kind (Phase 3)

Full guide, including failure, rollout and rollback demonstrations and troubleshooting: **[kubernetes/README.md](kubernetes/README.md)**.

**Prerequisites:** Docker Desktop running, `kind` (`brew install kind`), and kubectl 1.36 or newer (`brew install kubernetes-cli`; kind v0.33 runs Kubernetes 1.37). Stop Docker Compose first if it's running, because port-forwarding uses the same ports.

Create the cluster, build the images, load them into kind, and deploy:

```bash
kind create cluster --name cloudops-bridge
kubectl wait --for=condition=Ready node --all --timeout=120s
docker build -t cloudops-bridge-ticket-service:phase3 -f ticket_service/Dockerfile .
docker build -t cloudops-bridge-bridge:phase3 -f bridge/Dockerfile .
kind load docker-image cloudops-bridge-ticket-service:phase3 cloudops-bridge-bridge:phase3 --name cloudops-bridge
kubectl apply -f kubernetes/namespace.yaml -f kubernetes/monitoring/namespace.yaml
kubectl -n monitoring get secret grafana-admin || kubectl -n monitoring create secret generic grafana-admin --from-literal=admin-password="$(openssl rand -base64 24)"
kubectl apply -R -f kubernetes/
kubectl -n cloudops-bridge rollout status deployment/ticket-service --timeout=120s
kubectl -n cloudops-bridge rollout status deployment/bridge --timeout=120s
kubectl -n monitoring rollout status deployment/prometheus --timeout=300s
kubectl -n monitoring rollout status deployment/grafana --timeout=300s
kubectl -n monitoring rollout status deployment/alertmanager --timeout=300s
```

The second command generates a random Grafana admin password into a Kubernetes Secret only if it doesn't exist yet (it prints `NotFound` first on a fresh cluster). It's never stored in Git.

Inspect. Both Deployments should be `2/2`, with four pods `1/1 Running`:

```bash
kubectl -n cloudops-bridge get deployments,pods,services,configmaps
```

Test every endpoint from inside the cluster, through the Services:

```bash
kubectl -n cloudops-bridge exec -i deploy/bridge -- python - < kubernetes/tools/smoke_test.py
```

Or from your Mac. Run each port-forward in its own tab, then use the [example requests](#example-requests):

```bash
kubectl -n cloudops-bridge port-forward svc/ticket-service 8080:8080
```

```bash
kubectl -n cloudops-bridge port-forward svc/bridge 8081:8081
```

First places to look when something is wrong (details in [kubernetes/README.md](kubernetes/README.md#9-troubleshooting-commands-when-each-one-helps)):

```bash
kubectl -n cloudops-bridge get pods
kubectl -n cloudops-bridge describe pod POD_NAME
kubectl -n cloudops-bridge logs POD_NAME --previous
kubectl -n cloudops-bridge get events --sort-by=.lastTimestamp
kubectl -n cloudops-bridge rollout history deployment/ticket-service
```

Clean up. The cluster can be rebuilt from scratch with the commands above:

```bash
kind delete cluster --name cloudops-bridge
```

What Phase 3 demonstrates, all verified on a live cluster:

- **Liveness:** a frozen (`SIGSTOP`) uvicorn process is pulled from traffic by readiness in ~10 s and restarted by liveness in ~40 s.
- **Reconciliation:** a deleted pod is replaced because the Deployment declares 2 replicas.
- **Rolling update:** zero failed requests, measured from inside the cluster. This needed a `preStop` hook, because the first version dropped 4 requests to a termination race.
- **Rollback:** a broken image stalls the rollout without losing capacity (`maxUnavailable: 0`), and `rollout undo` restores it.
- **Security:** the containers run as UID 10001 with a read-only root filesystem, no capabilities, seccomp enabled and no privilege escalation. The ConfigMap mounts are read-only.

## Monitoring with Prometheus and Grafana (Phase 4)

Full guide, with discovery details, PromQL, controlled tests, security decisions and troubleshooting: **[kubernetes/monitoring/README.md](kubernetes/monitoring/README.md)**.

```
real request → ticket-service pod /metrics → Prometheus (scrapes every pod, found via the Kubernetes API) → PromQL → Grafana dashboard
```

Prometheus and Grafana deploy with everything else in the Phase 3 steps above (namespace `monitoring`). Then, with each port-forward in its own tab:

```bash
kubectl -n monitoring port-forward svc/prometheus 19090:9090
```

```bash
kubectl -n monitoring port-forward svc/grafana 13000:3000
```

Check that both ticket-service pods are discovered and `UP`, and send known traffic:

```bash
python3 kubernetes/tools/promql.py --targets
kubectl -n cloudops-bridge exec -i deploy/bridge -- python - 600 < kubernetes/tools/generate_traffic.py
python3 kubernetes/tools/promql.py 'sum by (path, status) (http_requests_total{job="ticket-service",path!~"/health|/ready|/metrics"})'
```

Copy the generated Grafana password to the clipboard without printing it, then open http://localhost:13000 and log in as `admin`:

```bash
kubectl -n monitoring get secret grafana-admin -o jsonpath='{.data.admin-password}' | base64 -d | pbcopy
```

What Phase 4 demonstrates, all verified on a live cluster:

- **Discovery:** Prometheus watches the Kubernetes API for `app.kubernetes.io/name=ticket-service` pods and scrapes each one directly. A replacement pod was a target within 3 seconds, with no config change.
- **Exact numbers:** 600 / 60 / 30 / 24 requests and 120 tickets sent; the same counts in Prometheus.
- **Honest multi-replica data:** `tickets_available` is shown per pod (for example 4942 and 4938), never summed, because each pod has its own inventory.
- **Failure visibility:** a frozen pod went to `up=0` within one scrape interval. In one run that was before Kubernetes marked it NotReady, in another after (the order depends on where each probe or scrape cycle falls), and `up` stayed 0 for a few seconds after the pod was Ready again. They're independent signals.
- **Real issues found and fixed:** Grafana 13's plugin installer broke the Prometheus data source under a read-only root filesystem; the Grafana image put its user in the root group; the Prometheus image's non-numeric user blocked `runAsNonRoot`.
- **Declarative:** the data source, dashboard and scrape config come from Git. Monitoring *history* is ephemeral by design.

Ports 19090 and 13000 are used because other local projects occupy 3000, 3001 and 9090–9094.

## Alerting with Prometheus rules and Alertmanager (Phase 5)

Full guide, with the alert lifecycle, rule semantics, label contract, routing, live demonstrations and measured timings: **[kubernetes/monitoring/README.md, section 10](kubernetes/monitoring/README.md#10-alerting-phase-5)**.

```
ticket-service /metrics → Prometheus evaluates rules every 15s → pending → firing → Alertmanager (grouped, tracked) → resolved
```

> Since Phase 6, ticket-api alerts are delivered to CloudOps Bridge (next section). There's still no email, chat or paging.

| Alert | Fires when | Live result |
|---|---|---|
| `TicketServiceTargetDown` | `up{job="ticket-service"} == 0` for 15s | Frozen pod: **pending** at +19–28 s, **firing** and in Alertmanager at +34–41 s, Kubernetes restarted it, **resolved** in both at +50–56 s (three runs) |
| `HighErrorRate` | 5xx are more than 5% of app requests over 2m, with traffic above 0.1 req/s, for 1m | Loaded and unit-tested (promtool). Stayed inactive under **7.5% 4xx** traffic, correctly. **Not fired live:** the app can't produce 5xx without an approved change |

Alerts carry `alertname`, `service="ticket-api"` and `environment="production"`, matching the service catalog, which the Bridge uses to map them to owners and runbooks. Alertmanager is reachable with `kubectl -n monitoring port-forward svc/alertmanager 19093:9093`, at http://localhost:19093.

Validate the configuration with the real tools (Docker required, venv active):

```bash
./kubernetes/monitoring/validate-alerting.sh
```

The `for` values are demo values, chosen to be observable before Kubernetes self-heals a pod. A one-off false positive during a rolling update showed why production uses minutes instead (documented in the guide).

## Incident enrichment: Alertmanager → CloudOps Bridge (Phase 6)

Full guide, with routing, payload handling, outcomes, status policy, logging, the trust boundary, the end-to-end demo and measured timings: **[kubernetes/monitoring/README.md, section 11](kubernetes/monitoring/README.md#11-incident-enrichment-alertmanager--cloudops-bridge-phase-6)**.

A raw alert, *"TicketServiceTargetDown on pod X"*, becomes an enriched incident:

```
status=firing  alert=TicketServiceTargetDown  service=ticket-api  environment=production  severity=warning
affected: pod=ticket-service-7fc4bd87d9-vh495  instance=10.244.0.13:8080
first responder: cloudops   owners: Ticket Development (app), CloudOps (platform)
dependencies: postgresql    endpoints: /health, /ready
runbook: ticket-service-target-down.md   + 6 suggested checks from the catalog
```

These values come from the alert's labels plus `service-catalog/ticket-api.yaml`. Nothing is hardcoded and nothing is diagnosed.

- **`POST /webhooks/alertmanager`** accepts Alertmanager's real v4 payload, processes **each alert individually** (firing and resolved), and maps by `alertname` / `service` / `environment`. Each alert's outcome is `enriched`, `unmapped` (a catalog gap: no invented runbook), or `rejected`.
- **`POST /incidents/enrich`** is unchanged, and shares the same lookup.
- **Verified live, three times** (once on a cluster rebuilt from scratch): a frozen pod led to Prometheus FIRING, which led to Alertmanager's **own** webhook. The Bridge logged `outcome=enriched` with the full context (~9 s after Alertmanager had the alert, `group_wait`). After recovery, the **resolved** webhook arrived 60 s after the firing one (`group_interval`), with the same fingerprint. Alertmanager recorded 0 delivery failures.
- **Found live:** a resolved webhook carried an *older* resolved alert again (Prometheus re-sends resolved alerts for 15 min), so the Bridge treats notifications as batches of independent alerts.
- **Limits:** nothing is persisted (logs and responses only); the webhook has no authentication (ClusterIP-only, trusted local cluster; not production-secure); only `ticket-api` is routed.

Watch deliveries from both Bridge pods:

```bash
kubectl -n cloudops-bridge logs -f -l app.kubernetes.io/name=bridge --prefix --since=1s | grep -E 'event=|POST /webhooks'
```

Try the webhook in isolation with a sample payload, for example under Docker Compose. This is **not** a real Alertmanager delivery:

```bash
curl -s -X POST localhost:8081/webhooks/alertmanager -H 'Content-Type: application/json' --data @tests/fixtures/alertmanager_target_down_firing.json | python3 -m json.tool
```

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

## Design notes

- **Catalog-driven, not hardcoded.** To add a service, add a YAML file. No Python changes are needed.
- **Severity depends on environment.** `HighErrorRate` is `critical` in production and `warning` in staging.
- **Documentation gaps are visible.** `HighLatency` deliberately has no runbook. The Bridge returns `runbook: null` and adds a reminder to document the fix afterward.
- **A test checks that every runbook referenced in the catalog exists**, so the catalog and the docs can't drift apart.
- **The inventory lives in memory** and resets on restart. On Kubernetes, each of the 2 ticket-service pods has its *own* inventory, so counts differ depending on which pod serves the request. PostgreSQL arrives in a later phase.
- **ConfigMaps are generated, not hand-written.** `kubernetes/generate-configmaps.sh` builds them from `service-catalog/` and `runbooks/`, and a test fails if they drift from those files.
- **The bridge has a separate `/ready`.** It returns 200 only when the catalog is loaded, and 503 otherwise. `/health` only means the process is alive.

## Tests

Tests run outside Docker, against the code directly:

```bash
source .venv/bin/activate
pip install -r requirements-dev.txt
pytest -v
```

68 pytest tests, plus promtool rule unit tests (`./kubernetes/monitoring/validate-alerting.sh`, needs Docker). The pytest tests cover health, readiness (including bridge `/ready` returning 503 without a catalog), tickets, valid and invalid purchases, the sold-out case, the metrics format, enrichment, environment-based severity, the missing-runbook case, unknown service, unknown environment or alert, missing or invalid fields, malformed JSON, catalog integrity, ConfigMaps matching their source files, and monitoring configuration: Prometheus discovery matching the Deployment's labels and port, counters always wrapped in `rate()`/`increase()`, per-pod inventory never aggregated, namespaced read-only RBAC, no committed Secrets, the Grafana plugin regression guard, and the Alertmanager webhook (real v4 payloads, per-alert outcomes, firing/resolved, batches, unknown/missing labels, malformed input, 503 when the catalog isn't loaded, log fields, HighErrorRate compatibility), and alerting wiring: Prometheus loads the mounted rules and targets the real Alertmanager Service, HighErrorRate counts only 5xx, TargetDown is scoped to ticket-service, alert labels match the service catalog, monitoring images are pinned, and Alertmanager sends only ticket-api alerts, only to the in-cluster Bridge webhook, with `send_resolved: true`, and every Prometheus alert has a catalog entry with matching severity.
