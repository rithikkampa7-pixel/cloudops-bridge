# CloudOps Bridge

**Operational context for on-call engineers, on a Kubernetes platform that monitors, alerts and autoscales a fictional ticketing service.**

A bare alert such as *"TicketServiceTargetDown on ticket-api in production"* tells an on-call engineer very little. CloudOps Bridge turns it into an **enriched incident**: who owns the service, who responds first, what it depends on, which runbook to open and what to check first. Around it, a local Kubernetes platform runs the ticketing workload with health probes, Prometheus monitoring, Alertmanager alerting and CPU-based autoscaling.

> Fictional demonstration project. All services, teams and data are made up. Everything runs locally on a single-node [kind](https://kind.sigs.k8s.io/) cluster; no cloud account is needed.

![Grafana: replicas scaling from 2 to 6 and back to 2, per-pod CPU against the 70% HPA target, and HPA utilization during a 200 req/s spike](docs/images/grafana-autoscaling.png)

*Grafana during the measured on-sale spike: the HPA scaled ticket-service from 2 to 6 replicas (its configured maximum) and back to 2. Real data from the acceptance run, 22:44–23:10 UTC.*

## CloudOps Bridge in 60 seconds

- **A sample ticketing application runs on Kubernetes.** Customers can browse and buy tickets through a small web API.
- **It grows with demand.** When many customers arrive at once, Kubernetes automatically starts more copies (pods) of the application, and removes them when traffic drops.
- **Problems are detected automatically.** Monitoring checks every copy of the application and raises an alert when one stops responding.
- **Alerts arrive with context.** CloudOps Bridge adds what the on-call engineer needs first: the responsible team, who responds first, what the service depends on, which runbook to open and what to check.

### What was verified

These results come from a **local Kubernetes demonstration on a single laptop**, not from a production environment. They show how the system behaves, not production capacity.

- **60,000 requests with zero failures** during a simulated ticket-sale spike (200 requests per second for five minutes), in the measured run.
- **Automatic scaling from 2 to 6 pods and back to 2** in the same run, without manual intervention.
- **A real service failure became an enriched incident.** A stopped application process triggered a real alert, which CloudOps Bridge enriched with incident information, followed by a resolved notification when Kubernetes recovered the pod.

### See it working

- [Grafana: autoscaling](docs/images/grafana-autoscaling.png): replicas, CPU per pod and the HPA target during the spike
- [Grafana: traffic and errors](docs/images/grafana-traffic-errors.png): request rate and status codes, with no errors
- [Grafana: full dashboard](docs/images/grafana-full-dashboard.png)
- [Real incident-enrichment example](#example-enriched-incident-real-values-from-the-rebuilt-cluster-run)
- [Architecture diagram](#architecture)
- [Demo walkthrough](docs/demo.md)

## The problem

On-call engineers lose the first minutes of an incident answering the same questions: which service, which environment, who owns it, what it depends on, where the runbook is, what to check first. CloudOps Bridge answers them from a **service catalog**, one YAML file per service that the Development and CloudOps teams agree on as their operational handoff. The knowledge lives in version control instead of in someone's head.

## Key capabilities

- **Incident enrichment.** Alertmanager sends real firing and resolved alerts to the Bridge's webhook. The Bridge maps the alert's labels (`alertname`, `service`, `environment`) to owners, first responder, dependencies, endpoints, runbook and suggested checks from `service-catalog/` and `runbooks/`. Unknown alerts are reported as `unmapped`; no runbook is invented.
- **Monitoring.** Prometheus discovers every ticket-service pod through the Kubernetes API and scrapes it directly. Grafana shows traffic, status codes, errors, per-pod inventory and scaling, provisioned from Git.
- **Alerting.** `TicketServiceTargetDown`, `HighErrorRate` and `TicketServiceRolloutStuck` rules, validated with `promtool` and `amtool`, with rule unit tests.
- **Autoscaling.** A HorizontalPodAutoscaler keeps ticket-service between 2 and 6 replicas at 70% of its CPU request, driven by real ticket traffic. No artificial CPU-burning endpoint exists.
- **Kubernetes operations.** Liveness and readiness probes, requests and limits, zero-drop rolling updates, rollback, and hardened, non-root containers.

## Architecture

```mermaid
flowchart LR
    LG["Load generator Job<br/>20 → 200 → 20 req/s"] -->|HTTP| TS

    subgraph app["namespace cloudops-bridge"]
        TS["Ticket Service<br/>FastAPI, 2–6 pods"]
        BR["CloudOps Bridge<br/>FastAPI, 2 pods"]
        CAT[("Service catalog<br/>and runbooks")]
    end

    MS["metrics-server"] -->|pod CPU| HPA["HorizontalPodAutoscaler<br/>2–6 replicas at 70% CPU"]
    HPA -->|scales| TS

    subgraph mon["namespace monitoring"]
        P["Prometheus"]
        G["Grafana"]
        AM["Alertmanager"]
        KSM["kube-state-metrics"]
    end

    TS -->|/metrics| P
    KL["kubelet<br/>container CPU"] --> P
    KSM -->|replicas, HPA status| P
    P -->|PromQL| G
    P -->|firing / resolved alerts| AM
    AM -->|webhook| BR
    CAT --> BR
    BR --> INC["Enriched incident<br/>owners, runbook, checks"]
```

**Responsibilities:** Prometheus *detects*, Alertmanager *delivers*, the Bridge *explains*. The Bridge doesn't diagnose root causes or remediate anything.

## Results: autoscaling under a ticket on-sale spike

In a local single-node kind experiment, a 200 req/s ticket-traffic spike drove HPA scaling from 2 to 6 pods; all 60,000 spike requests completed successfully in that run. The traffic was real `GET /tickets` browsing plus 5% single-ticket purchases from an in-cluster load generator. The cluster was freshly built using only the documented commands.

| Time after spike start | Event |
|---|---|
| +24 s | HPA scaled **2 → 4** (CPU 111% of request vs 70% target) |
| +39 s | HPA scaled **4 → 6**, the configured maximum (CPU 247%) |
| +51 s | **All six pods Ready** |
| after the spike | Scale-down after the 300 s stabilization window: **6 → 4 → 3 → 2** |

| Stage | Configured | Achieved | Requests | Failed |
|---|---|---|---|---|
| Normal | 20 req/s for 3 min | 20.0 req/s | 3,600 | 0 |
| Spike | 200 req/s for 5 min | 200.0 req/s | 60,000 | 0 |
| Recovery | 20 req/s for 8 min | 20.0 req/s | 9,600 | 0 |

- An **independent availability checker** ran separately for the whole event (35 min, including every scale-down) and measured **10,197 successful checks and 0 failures**.
- **Six replicas was the configured maximum, and CPU remained above the 70% target during the spike.** More capacity would have been needed to reach the target.
- This is one run on one laptop node. It shows how Kubernetes reacts to a spike; it is **not** a benchmark or a production capacity result.

![Grafana: request rate up to 200 req/s, status codes 200 and 201 only, 4xx and 5xx flat at zero, tickets sold per minute](docs/images/grafana-traffic-errors.png)

*Same window: request rate, responses by status code (only 200 and 201), and 4xx/5xx errors at zero throughout.*

Full method, timeline, per-stage numbers and findings: [kubernetes/README.md, section 11](kubernetes/README.md#11-autoscaling-hpa).

## Results: incident enrichment

When Prometheus detects an unavailable ticket-service target, Alertmanager sends a real webhook to CloudOps Bridge, which maps stable alert labels to operational context from the service catalog and runbooks.

Verified live multiple times, including with HPA enabled, by freezing one ticket-service process:

- Prometheus fired `TicketServiceTargetDown`, and Alertmanager's own webhook reached the Bridge about 9 s after Alertmanager received the alert (its `group_wait`). The Bridge logged `outcome=enriched` with the full context.
- After Kubernetes restarted the pod, the **resolved** webhook arrived 60 s after the firing one (`group_interval`), with the same alert fingerprint.
- Alertmanager recorded 0 failed deliveries.

### Example enriched incident (real values from the rebuilt-cluster run)

| Field | Value |
|---|---|
| Alert | `TicketServiceTargetDown`, status `firing`, severity `warning` |
| Service / environment | `ticket-api` / `production` |
| Affected | pod `ticket-service-79b4b8b84d-5x6wk`, instance `10.244.0.10:8080` |
| First responder | `cloudops` |
| Application owner | Ticket Development |
| CloudOps owner | CloudOps |
| Dependency | `postgresql` (declared in the catalog; not deployed in this project) |
| Health / readiness | `/health`, `/ready` |
| Runbook | [`ticket-service-target-down.md`](runbooks/ticket-service-target-down.md) |
| Fingerprint | `c850a769b919ceef` |

Suggested checks, from the catalog:

1. Check whether Kubernetes already restarted or replaced the pod (RESTARTS, pod age)
2. Check the pod's readiness and liveness state and recent events
3. Read the scrape error on the Prometheus target (timeout vs connection refused)
4. Read the previous container's logs if the pod restarted
5. Confirm the other replica is Ready and serving traffic
6. Check rollout history for a deployment in progress

<details>
<summary>The Bridge's log lines for this incident</summary>

```
2026-10-08 21:34:55,502 INFO bridge event=webhook_received receiver=cloudops-bridge group_status=firing alerts=1 truncated=0
2026-10-08 21:34:55,506 INFO bridge event=alert_processed status=firing outcome=enriched alertname=TicketServiceTargetDown service=ticket-api environment=production severity=warning pod=ticket-service-79b4b8b84d-5x6wk instance=10.244.0.10:8080 runbook=ticket-service-target-down.md fingerprint=c850a769b919ceef reason=-
2026-10-08 21:34:55,506 INFO bridge event=incident_context fingerprint=c850a769b919ceef severity=warning first_responder=cloudops application_owner="Ticket Development" cloudops_owner=CloudOps dependencies=postgresql health=/health readiness=/ready runbook=ticket-service-target-down.md checks=6
2026-10-08 21:35:55,507 INFO bridge event=webhook_received receiver=cloudops-bridge group_status=resolved alerts=1 truncated=0
2026-10-08 21:35:55,508 INFO bridge event=alert_processed status=resolved outcome=enriched alertname=TicketServiceTargetDown service=ticket-api environment=production severity=warning pod=ticket-service-79b4b8b84d-5x6wk instance=10.244.0.10:8080 runbook=ticket-service-target-down.md fingerprint=c850a769b919ceef reason=-
```

</details>

Full routing, payload handling, outcomes and the end-to-end procedure: [kubernetes/monitoring/README.md, section 11](kubernetes/monitoring/README.md#11-incident-enrichment-alertmanager--cloudops-bridge).

## Engineering findings

Problems found by testing on a live cluster, and what changed because of them:

1. **Rolling updates dropped requests.** The first rolling update dropped 4 of 198 requests, even though it never went below 2 Ready pods: Kubernetes sends SIGTERM at the same time as it removes the pod from the Service, so the process exited before routing stopped sending it traffic. A 5 s `preStop` hook fixed it; every rollout test since measured 0 failed requests from inside the cluster.
2. **`kubectl rollout undo` can redeploy the broken release.** After an earlier rollback, `undo` went back to the bad image. The runbook now says to check the history and roll back to an explicitly chosen known-good revision.
3. **Alertmanager notifications aren't one-to-one with events.** A resolved notification also carried an older, already-resolved alert. The Bridge therefore processes every alert in a notification individually and keeps no state between notifications.
4. **Handing replica ownership to the HPA can drop a service to 1 pod.** Removing `replicas` from a Deployment created with it makes `kubectl apply` delete the field, and Kubernetes falls back to 1 replica. Running `kubectl apply set-last-applied` first made the transition safe: verified at 2/2 with the same pods throughout.

Short-lived target-down states also showed why alert timing matters: one alert fired for a terminating pod during a rollout, and during scale-up a brand-new pod was scraped before it listened (pending for about 15 s, never fired). The 15 s `for` values here are demo values; production uses minutes.

## Quick start (kind)

**Prerequisites:** Docker Desktop running, `kind` (`brew install kind`), and kubectl 1.36 or newer (`brew install kubernetes-cli`). Code blocks contain only commands, so they paste cleanly into zsh.

```bash
git clone https://github.com/rithikkampa7-pixel/cloudops-bridge.git
cd cloudops-bridge
kind create cluster --name cloudops-bridge
kubectl wait --for=condition=Ready node --all --timeout=120s
docker build -t cloudops-bridge-ticket-service:phase3 -t cloudops-bridge-ticket-service:phase8-v2 -f ticket_service/Dockerfile .
docker build -t cloudops-bridge-bridge:phase3 -f bridge/Dockerfile .
kind load docker-image cloudops-bridge-ticket-service:phase3 cloudops-bridge-ticket-service:phase8-v2 cloudops-bridge-bridge:phase3 --name cloudops-bridge
kubectl apply -f kubernetes/namespace.yaml -f kubernetes/monitoring/namespace.yaml
kubectl -n monitoring get secret grafana-admin || kubectl -n monitoring create secret generic grafana-admin --from-literal=admin-password="$(openssl rand -base64 24)"
kubectl apply -R -f kubernetes/
kubectl -n cloudops-bridge rollout status deployment/ticket-service --timeout=120s
kubectl -n cloudops-bridge rollout status deployment/bridge --timeout=120s
kubectl -n monitoring rollout status deployment/prometheus --timeout=300s
kubectl -n monitoring rollout status deployment/grafana --timeout=300s
kubectl -n monitoring rollout status deployment/alertmanager --timeout=300s
kubectl -n kube-system rollout status deployment/metrics-server --timeout=300s
kubectl -n monitoring rollout status deployment/kube-state-metrics --timeout=300s
kubectl -n cloudops-bridge wait --for=jsonpath='{.status.readyReplicas}'=2 deployment/ticket-service --timeout=300s
```

The Grafana admin password is generated into a Kubernetes Secret at deploy time and never stored in Git (the `get secret` prints `NotFound` first on a fresh cluster). The last `wait` matters because the HPA owns the replica count: a fresh Deployment starts at 1 replica and the HPA raises it to 2. The `:phase3` image tags are historical names. The ticket-service image is also tagged `:phase8-v2`, the tag its Deployment uses since the [GitOps deployment](gitops/README.md); both tags are the same build, and the load test still uses `:phase3`.

Test every endpoint from inside the cluster, then open Grafana (port-forward in its own tab, log in as `admin` with the password copied to your clipboard):

```bash
kubectl -n cloudops-bridge exec -i deploy/bridge -- python - < kubernetes/tools/smoke_test.py
kubectl -n monitoring get secret grafana-admin -o jsonpath='{.data.admin-password}' | base64 -d | pbcopy
kubectl -n monitoring port-forward svc/grafana 13000:3000
```

Then open http://localhost:13000. Ports 19090 (Prometheus), 13000 (Grafana) and 19093 (Alertmanager) avoid clashing with tools commonly running on the default ports.

Run the on-sale scenario (about 16 minutes of traffic, then up to 5 minutes more for scale-down) and watch the HPA's decisions:

```bash
kubectl apply -f loadtest/loadgen-configmap.yaml
kubectl -n cloudops-bridge delete job ticket-sale --ignore-not-found
kubectl create -f loadtest/ticket-sale-job.yaml
python3 kubernetes/tools/scale_watch.py 1200 5
```

Clean up with `kind delete cluster --name cloudops-bridge`. Monitoring history lives only inside the cluster and is deleted with it.

## Demo

[docs/demo.md](docs/demo.md) is a guided walkthrough of the verified procedures: the dashboard, the on-sale autoscaling scenario, and the real Alertmanager-to-Bridge incident path.

To run just the two APIs without Kubernetes (local Python or Docker Compose), with example requests and error responses, see [docs/local-development.md](docs/local-development.md).

## Technology

| Area | Tools |
|---|---|
| Applications | Python 3.13, FastAPI, Pydantic, uvicorn; YAML service catalog, Markdown runbooks |
| Containers | Docker, Docker Compose; pinned `python:3.13.16-slim-trixie` base image |
| Kubernetes | kind (Kubernetes 1.37), Deployments, Services, ConfigMaps, HorizontalPodAutoscaler, metrics-server v0.9.0 |
| Observability | Prometheus v3.14.0, Grafana 13.2.2, Alertmanager v0.34.1, kube-state-metrics v2.20.0 |
| Testing | pytest, promtool, amtool |

Plain manifests, no Helm: every object is visible and reviewed, and charts would install components this project doesn't need.

## Tests

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements-dev.txt
pytest -v
./kubernetes/monitoring/validate-alerting.sh
```

85 pytest tests, plus `promtool`/`amtool` validation and rule unit tests (the last command, which needs Docker).

| Area | What the tests protect |
|---|---|
| APIs | Health and readiness, ticket listing and purchases, sold-out and invalid input, metrics format, enrichment, environment-based severity, unknown service/environment/alert, missing runbook, malformed JSON |
| Catalog and runbooks | The catalog validates; every runbook it references exists; generated ConfigMaps match their sources |
| Alertmanager webhook | Real v4 payloads, per-alert outcomes, firing and resolved, batches, unknown or missing labels, malformed input, 503 without a catalog, log fields |
| Monitoring and alerting | Prometheus discovery matches the Deployment, counters always use `rate()`/`increase()`, per-pod inventory is never summed, every dashboard metric comes from a configured scrape job, alert labels match the catalog, Alertmanager routes only ticket-api to the in-cluster Bridge |
| Autoscaling | The HPA targets the Deployment on CPU, a CPU request exists, the Deployment has no `replicas` field, metrics-server differs from upstream only by one kind flag (checksum-verified), the load generator's profile, request mix, failure categories and summaries |
| Security | No committed Secrets, pinned images, exact RBAC rules |

**Generated files are committed on purpose.** The ConfigMaps under `kubernetes/config/`, the Grafana dashboard ConfigMap and the load-generator ConfigMap are generated by `kubernetes/generate-configmaps.sh` from `service-catalog/`, `runbooks/`, `dashboards/` and `loadtest/`. They're committed so `kubectl apply -R` works from a clean checkout, and a test fails if any of them drifts from its source.

## Security considerations

- **Containers:** non-root (UID 10001 for the apps), read-only root filesystem, all capabilities dropped, no privilege escalation, `RuntimeDefault` seccomp. The catalog and runbooks are mounted read-only.
- **No secrets in Git.** The only credential, Grafana's admin password, is generated at deploy time into a Kubernetes Secret; a test fails if a Secret is committed.
- **Least-privilege RBAC.** Prometheus discovers pods through a namespaced, read-only Role. Its only cluster-wide permission, needed for kubelet CPU metrics, is listing nodes and reading `nodes/metrics`, never `nodes/proxy`. Its scrape keeps only two CPU and memory metrics for the app namespace; that limits what is *stored*, not what the permission can *read*. kube-state-metrics uses a namespaced list/watch Role.
- **Pinned images** everywhere; metrics-server is vendored from the official release and checksum-verified.
- **Local-only shortcuts:** on kind, metrics-server and Prometheus don't verify the kubelet's TLS certificate (`--kubelet-insecure-tls`, `insecure_skip_verify`). The Bridge webhook has **no authentication**; it is reachable only inside the cluster (ClusterIP). Neither is acceptable in production.

## Limitations

- **Local, single node.** All results come from one kind node on a laptop. They show Kubernetes behavior, not production capacity or performance.
- **The HPA hit its ceiling.** During the spike, 6 replicas (the configured maximum) still ran above the 70% CPU target.
- **Per-replica in-memory inventory.** Each ticket-service pod has its own 5,000 tickets. New pods start fresh and removed pods lose their sales, so pods disagree and totals can't be summed. Shared state (PostgreSQL) isn't implemented.
- **`HighErrorRate` hasn't fired live.** It is unit-tested and stayed correctly silent under 4xx traffic, but the app has no safe way to produce 5xx without adding a failure-injection endpoint, which this project deliberately doesn't do. A `HighLatency` alert isn't possible yet: the app exports no latency metric.
- **No persistence or notification channels.** Incidents exist only in the Bridge's logs and HTTP responses. There's no email, chat or paging, and only `ticket-api` alerts are routed to the Bridge.
- **Demo alert timings.** The 15 s `for` values are chosen to be observable before Kubernetes self-heals a pod; one false positive was observed during a rolling update.
- **Monitoring history is ephemeral**: Prometheus and Grafana data are lost when the cluster is deleted. Configuration comes from Git.
- **Context, not diagnosis.** The Bridge doesn't determine root causes or remediate.

## Repository structure

```
cloudops-bridge/
├── ticket_service/          # demo Ticket Service (FastAPI): API, in-memory inventory, /metrics
├── bridge/                  # CloudOps Bridge (FastAPI): catalog loading, enrichment, Alertmanager webhook
├── service-catalog/         # one YAML file per service: the Dev/CloudOps handoff contract
├── runbooks/                # Markdown runbooks referenced by the catalog
├── dashboards/              # Grafana dashboard JSON (source of truth)
├── kubernetes/              # manifests, ConfigMap generator, demo tools; guide in kubernetes/README.md
│   ├── monitoring/          # Prometheus, Grafana, alert rules, Alertmanager, kube-state-metrics
│   ├── metrics-server/      # vendored metrics-server for the HPA
│   └── tools/               # smoke test, availability checker, traffic, PromQL, alert and scaling watchers
├── loadtest/                # on-sale load generator Job (outside kubernetes/, so apply -R never starts it)
├── tests/                   # pytest suite, promtool rule tests, sample Alertmanager payload
├── docs/                    # demo walkthrough, local development, images
└── compose.yaml             # runs the two applications with Docker Compose
```

## Detailed documentation

- **[kubernetes/README.md](kubernetes/README.md):** deploying to kind; security verification; failure, rollout and rollback demonstrations; troubleshooting; autoscaling method and measured results.
- **[kubernetes/monitoring/README.md](kubernetes/monitoring/README.md):** Prometheus discovery and PromQL, the Grafana dashboard, alert rules and Alertmanager, incident enrichment end to end, scaling metrics.
- **[docs/demo.md](docs/demo.md):** guided walkthrough.
- **[docs/local-development.md](docs/local-development.md):** local Python and Docker Compose, example requests, error handling.

## Build history

The project was built in stages; each was verified on a live cluster before it was committed.

| Stage | Commit | Added |
|---|---|---|
| 1 | `59620c2` | Ticket Service, CloudOps Bridge, service catalog, runbook, tests |
| 2 | `bb95b45` | Docker images and Docker Compose |
| 3 | `1897a69` | Kubernetes on kind: probes, resources, security context, rollouts and rollback |
| 4 | `d5eb3b8` | Prometheus and Grafana |
| 5 | `bc30a51` | Alert rules and Alertmanager |
| 6 | `cc926c0` | Alertmanager → CloudOps Bridge incident enrichment |
| 7 | `9cfde3d` | HorizontalPodAutoscaler and the ticket on-sale load test |
