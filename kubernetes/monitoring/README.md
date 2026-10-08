# Monitoring, alerting and incident enrichment (Phases 4–6)

Prometheus discovers and scrapes every ticket-service pod and evaluates alert rules. Grafana reads from Prometheus and shows one provisioned dashboard. Alertmanager receives the alerts Prometheus fires, groups them, and tracks them until they resolve. Alertmanager sends ticket-api alerts to CloudOps Bridge, which enriches them with catalog context. Prometheus, Grafana and Alertmanager run inside the kind cluster, in the `monitoring` namespace. Alerting is covered in [section 10](#10-alerting-phase-5) and enrichment in [section 11](#11-incident-enrichment-alertmanager--cloudops-bridge-phase-6).

> Every code block contains only commands (no `#` comments), so blocks paste cleanly into zsh. Run commands from the repository root, `~/cloudops-bridge`, after the cluster and apps from [../README.md](../README.md) are deployed.

```
 real request ──► ticket-service pod ──► /metrics (counters and gauges change)
                        ▲
                        │ scrape every 15s, pod IP:8080/metrics
                        │ (targets found by watching the Kubernetes API)
                 ┌──────┴──────┐        PromQL        ┌───────────┐
                 │ Prometheus  │ ◄─────────────────── │  Grafana  │ ◄── you, on localhost:13000
                 │ :9090       │                       │  :3000    │
                 └──────┬──────┘                       └───────────┘
                  localhost:19090                       provisioned data source + dashboard
                        │ firing / resolved alerts (Phase 5)
                        ▼
                 ┌──────────────┐
                 │ Alertmanager │ ◄── localhost:19093 (UI/API)
                 │ :9093        │
                 └──────┬───────┘
                        │ webhook, firing + resolved (Phase 6)
                        ▼
                 ┌─────────────────┐      service-catalog/*.yaml
                 │ CloudOps Bridge │ ◄─── runbooks/*.md (ConfigMaps)
                 │ /webhooks/...   │ ──►  enriched incident (response + logs)
                 └─────────────────┘
```

| Implemented | Still future |
|---|---|
| Prometheus, Kubernetes service discovery and scraping, PromQL, Grafana, provisioned Prometheus data source, provisioned CloudOps dashboard (Phase 4). Alert rules `HighErrorRate` and `TicketServiceTargetDown`, Alertmanager with grouping and alert lifecycle, promtool/amtool validation (Phase 5). Alertmanager → CloudOps Bridge webhook with catalog enrichment of firing and resolved alerts (Phase 6) | Any notification channel (email, Slack, paging), incident persistence/history, webhook authentication, a `HighLatency` alert (no latency metric exists), HPA, Locust, PostgreSQL, Argo CD, GitHub Actions, Terraform, Ansible |

## Files

```
kubernetes/monitoring/
├── namespace.yaml                         namespace monitoring
├── prometheus/rbac.yaml                   ServiceAccount + namespaced Role (read pods in cloudops-bridge only)
├── prometheus/configmap.yaml              prometheus.yml: scrape jobs, discovery, rule files, Alertmanager target
├── prometheus/rules-configmap.yaml        alert rules (Phase 5), the single source of truth
├── alertmanager/configmap.yaml            alertmanager.yml: grouping; ticket-api → Bridge webhook (Phases 5–6)
├── alertmanager/deployment.yaml, service.yaml
├── validate-alerting.sh                   promtool + amtool checks and rule unit tests (Phase 5)
├── prometheus/deployment.yaml, service.yaml
├── grafana/datasource-configmap.yaml      provisions the Prometheus data source
├── grafana/dashboard-provider-configmap.yaml
├── grafana/dashboard-configmap.yaml       GENERATED from dashboards/ticket-service-overview.json
└── grafana/deployment.yaml, service.yaml
dashboards/ticket-service-overview.json    dashboard source of truth (outside kubernetes/: apply -R would treat .json as a manifest)
kubernetes/tools/generate_traffic.py       deterministic traffic, run in-cluster
kubernetes/tools/promql.py                 compact PromQL / target / rule queries from your Mac
kubernetes/tools/alert_watch.py            timeline of up, pod state, Prometheus and Alertmanager alerts (Phase 5)
tests/prometheus/ticket-service-alerts.test.yml   promtool unit tests for the rules (Phase 5)
```

## Design decisions

**Plain manifests, not Helm.** Helm isn't installed. The community `prometheus` chart enables Alertmanager, node-exporter, kube-state-metrics and Pushgateway by default, and `kube-prometheus-stack` adds the Prometheus Operator and its custom resources. This phase needs one Prometheus and one Grafana. Ten short manifests are easier to read and explain, and they match Phase 3 (no Helm or Kustomize). In production I'd use the Helm chart or the Operator for upgrades and sane defaults.

**Pinned images:** `prom/prometheus:v3.14.0` and `grafana/grafana:13.2.2`. The kind node pulls them from Docker Hub on first deploy, so it needs internet access once.

**How discovery works.** `prometheus.yml` has a job `ticket-service` with `kubernetes_sd_configs` `role: pod`:

1. Prometheus *watches* the Kubernetes API for pods in namespace `cloudops-bridge` labeled `app.kubernetes.io/name=ticket-service`. The label filter runs on the API server, so Prometheus only receives matching pods.
2. Each pod's container ports become candidate targets. Relabeling keeps only the port named `http` (8080) and only pods in phase `Running`, so a Pending pod with no IP is skipped.
3. Prometheus scrapes `http://<pod IP>:8080/metrics`. That's **each pod directly, not through the Service**, so every replica is scraped separately.
4. Relabeling copies the pod name to a `pod` label and the namespace to `namespace`, so every series says which replica produced it.
5. When a pod is replaced, the watch delivers the change within seconds: the new pod becomes a target and the old one disappears. No IPs are hardcoded and nothing is reloaded.

RBAC: the `prometheus` ServiceAccount (namespace `monitoring`) gets a **Role in `cloudops-bridge`** with get/list/watch on pods, and nothing cluster-wide.

**Two jobs, so targets are easy to tell apart.** `job="ticket-service"` is the application. `job="prometheus"` is Prometheus scraping itself. Every dashboard query filters on `job="ticket-service"`.

**Local ports.** The other projects on this machine already use 3000, 3001 and 9090–9094, so port-forwards use **19090** (Prometheus), **13000** (Grafana) and **19093** (Alertmanager).

## 1. Install

The Grafana admin password is generated randomly into a Kubernetes Secret at deploy time and is **never stored in Git**. The command only creates it if it doesn't exist yet, so re-running it keeps the same password. A Secret's value is only **base64-encoded, not encrypted**: anyone who can read Secrets in the namespace can decode it. That's acceptable for a local demo; a real cluster would add encryption at rest, RBAC on Secrets, or an external secret manager.

If you followed [../README.md](../README.md) section 4, this is already done. Otherwise:

```bash
kubectl apply -f kubernetes/namespace.yaml -f kubernetes/monitoring/namespace.yaml
kubectl -n monitoring get secret grafana-admin || kubectl -n monitoring create secret generic grafana-admin --from-literal=admin-password="$(openssl rand -base64 24)"
kubectl apply -R -f kubernetes/
kubectl -n monitoring rollout status deployment/prometheus --timeout=300s
kubectl -n monitoring rollout status deployment/grafana --timeout=300s
kubectl -n monitoring rollout status deployment/alertmanager --timeout=300s
```

The first `get secret` prints `NotFound` on a fresh cluster. That's expected, and it's what triggers the `create`.

## 2. Verify Prometheus

Pod status and logs:

```bash
kubectl -n monitoring get pods -o wide
kubectl -n monitoring logs deploy/prometheus | grep -E 'level=(ERROR|WARN)|discovery|Server is ready'
```

Expected: `prometheus-...` `1/1 Running`. The log shows `Using pod service account via in-cluster config ... config=ticket-service` and `Server is ready to receive web requests`, with no `ERROR` lines. If you see `forbidden`, the RBAC Role or RoleBinding is wrong.

Start a port-forward in its own tab and leave it running:

```bash
kubectl -n monitoring port-forward svc/prometheus 19090:9090
```

The web UI is at http://localhost:19090. Status → Target health lists the same targets as below.

Discovered targets:

```bash
python3 kubernetes/tools/promql.py --targets
```

Expected: one `prometheus` target, and **two** `ticket-service` targets (one per pod, with pod names and pod IPs), all `UP`.

Scrape health. Each target produces an `up` series: 1 means the last scrape succeeded, 0 means it failed:

```bash
python3 kubernetes/tools/promql.py 'up'
python3 kubernetes/tools/promql.py 'up{job="ticket-service"}'
```

Application metrics. These are real samples, one series per pod:

```bash
python3 kubernetes/tools/promql.py 'tickets_available{job="ticket-service"}'
python3 kubernetes/tools/promql.py 'tickets_sold_total{job="ticket-service"}'
python3 kubernetes/tools/promql.py 'http_requests_total{job="ticket-service"}'
```

Before any traffic, `http_requests_total` only contains `/health`, `/ready` and `/metrics`. The app counts Kubernetes probes and Prometheus scrapes too, which is why the dashboard filters them out.

The same queries over raw HTTP, if you prefer the JSON:

```bash
curl -s 'http://localhost:19090/api/v1/query' --data-urlencode 'query=up{job="ticket-service"}' | python3 -m json.tool
```

## 3. Verify Grafana

```bash
kubectl -n monitoring get pods
kubectl -n monitoring logs deploy/grafana | grep -E 'level=error' | head
```

Expected: `grafana-...` `1/1 Running` and **no** `level=error` lines. In particular, there should be no `Failed to install plugin` (see Troubleshooting).

Start a port-forward in its own tab:

```bash
kubectl -n monitoring port-forward svc/grafana 13000:3000
```

Copy the admin password to the macOS clipboard **without printing it**:

```bash
kubectl -n monitoring get secret grafana-admin -o jsonpath='{.data.admin-password}' | base64 -d | pbcopy
```

Open http://localhost:13000, log in as `admin`, and paste the password. The home page **is** the dashboard *CloudOps Bridge - Ticket Service Overview*. It's also under Dashboards → CloudOps Bridge.

Check the provisioned data source from the terminal. This reads the password into a shell variable, uses it, and then discards it:

```bash
GPW=$(kubectl -n monitoring get secret grafana-admin -o jsonpath='{.data.admin-password}' | base64 -d)
curl -s -u "admin:$GPW" localhost:13000/api/datasources/uid/prometheus/health
curl -s -u "admin:$GPW" 'localhost:13000/api/search?query=Ticket'
unset GPW
```

Expected: `"message":"Successfully queried the Prometheus API."` and the dashboard listed with uid `cloudops-ticket-service` in folder `CloudOps Bridge`.

In the UI, Connections → Data sources → Prometheus shows *"This data source was added by config and cannot be modified using the UI"*. That's provisioning.

## 4. Generate traffic and confirm exact counts

`generate_traffic.py` sends a **fixed** mix through the ticket-service Service at 10 requests per second, so the expected counts are known in advance. With the default 600 iterations:

| Request | Count | Expected status |
|---|---|---|
| `GET /tickets` | 600 | 200 |
| `POST /tickets/purchase` `{"quantity": 2}` | 60 (120 tickets) | 201 |
| `POST /tickets/purchase` `{"quantity": 0}` | 30 | 422, expected client error |
| `GET /no-such-page` | 24 | 404, expected client error (labeled `path="unmatched"`) |

Record the counters **before**:

```bash
python3 kubernetes/tools/promql.py 'sum by (path, status) (http_requests_total{job="ticket-service",path!~"/health|/ready|/metrics"})'
python3 kubernetes/tools/promql.py 'sum(tickets_sold_total{job="ticket-service"})'
```

On a fresh cluster the first query prints `(no data)`. Those series don't exist until the first such request.

Send the traffic (about 60 seconds). Watch the dashboard while it runs:

```bash
kubectl -n cloudops-bridge exec -i deploy/bridge -- python - 600 < kubernetes/tools/generate_traffic.py
```

Wait 20 seconds for the next scrape, then run the same two queries again. **Each difference should match the table exactly**, as long as no ticket-service pod restarted in between (a restart resets its counters).

Raw counter differences are used here, not `increase()`, because `increase()` extrapolates to the edges of its window and returns estimates such as 127.7 for 120.

See per-pod inventory diverge:

```bash
python3 kubernetes/tools/promql.py 'tickets_available{job="ticket-service"}'
python3 kubernetes/tools/promql.py 'tickets_sold_total{job="ticket-service"}'
```

Each pod's `tickets_available` equals 5000 minus *its own* `tickets_sold_total`. For example, one pod sold 58 and shows 4942, while the other sold 62 and shows 4938. **There's no shared inventory.** Each replica decrements its own in-memory counter, depending on which pod the Service routed each purchase to. The dashboard therefore shows one line per pod and never sums them. PostgreSQL (future phase) fixes this.

Request rate during traffic (run it while the generator is still sending):

```bash
python3 kubernetes/tools/promql.py 'sum by (path) (rate(http_requests_total{job="ticket-service",path!~"/health|/ready|/metrics"}[1m]))'
```

Expected while traffic runs: `/tickets` approaching 10 req/s, `/tickets/purchase` about 1.5, and `unmatched` about 0.4. The values climb during the first minute because `rate()` averages over a 1-minute window.

## 5. The dashboard and its queries

Every query filters `job="ticket-service"`. "App traffic" excludes `/health`, `/ready` and `/metrics`, because the app counts probes and scrapes too.

| Panel | Query (shortened) | Why it's built this way |
|---|---|---|
| Scrape targets UP | `count(up{job="ticket-service"} == 1)` | Should equal 2. Turns red below 2 |
| Target health by pod | `up{job="ticket-service"}` | Per pod. 1 → UP, 0 → DOWN. This is **scrape** health, not Kubernetes readiness |
| Tickets sold (time range) | `sum(increase(tickets_sold_total[$__range]))` | `increase()` handles counter resets. It's an extrapolated **estimate**, labeled as such |
| Server error ratio | 5xx rate ÷ all app request rate (5m) | Only 5xx counts as a service failure. Blank with no traffic (0/0) |
| Request rate by endpoint | `sum by (method, path) (rate(http_requests_total{app}[$__rate_interval]))` | A raw counter only grows, so `rate()` turns it into req/s. Summed across pods, because requests add up |
| Responses by status | `sum by (status) (rate(...))` | Status mix at a glance |
| 4xx vs 5xx | two series, each with `or vector(0)` | **Kept separate on purpose.** 4xx means the service correctly *rejected* a request (422 validation, 409 sold out, 404 unknown URL): expected, but spikes matter. 5xx means the service itself *failed*: always bad. Combining them would make normal validation look like an outage. `or vector(0)` draws 0 instead of a gap when a status never occurred |
| Tickets sold per minute | `sum(rate(tickets_sold_total[$__rate_interval])) * 60` | Summing across pods is valid: sales add up |
| Tickets available by pod | `tickets_available{job="ticket-service"}` | A gauge, shown raw, **one line per pod**, never summed or averaged |

`$__rate_interval` is Grafana's recommended rate window. It's derived from the data source's `timeInterval: 15s` (the scrape interval), so `rate()` always spans enough samples.

**Counter resets:** when a pod restarts, its `*_total` counters restart at 0. `rate()` and `increase()` detect the drop and treat it as a reset, not as negative traffic, which is why no panel graphs a raw counter. `tests/test_monitoring_config.py` fails if a raw `*_total` ever appears in the dashboard, or if `tickets_available` is aggregated.

## 6. Controlled tests

> These deliberately break a pod. They're safe on this local cluster and recover automatically. Keep the Prometheus port-forward running. If a test replaces the ticket-service pod your *app* port-forward was attached to, restart that port-forward. The Prometheus and Grafana port-forwards are unaffected.

### Test A: pod replacement is discovered automatically

Record the current targets, then delete one ticket-service pod:

```bash
python3 kubernetes/tools/promql.py --targets
kubectl -n cloudops-bridge delete pod $(kubectl -n cloudops-bridge get pods -l app.kubernetes.io/name=ticket-service -o jsonpath='{.items[0].metadata.name}')
```

Run this a few times over about 15 seconds:

```bash
kubectl -n cloudops-bridge get pods -l app.kubernetes.io/name=ticket-service
python3 kubernetes/tools/promql.py --targets
```

What you'll see, measured across runs:

| After deletion | Targets list (`--targets`) | `up{job="ticket-service"}` query |
|---|---|---|
| ~3 s | New pod listed as `UNKNOWN` (discovered, not scraped yet), even while Kubernetes still shows it `0/1` | 2 series |
| 0–12 s | Old pod still `UP` while `Terminating`, because its 5 s preStop hook keeps it serving | 2 series |
| ~12 s | Old pod gone from the list | 2 series |
| ~14 s | Two `UP` targets: survivor plus new pod | **3 series**: the new pod's first sample, plus the old pod's last sample, which is still current |
| ~34 s | Same | **2 series**: Prometheus has written the old series' *staleness marker* |

The ~20-second overlap is normal Prometheus behavior: a removed target's series is marked stale about one scrape cycle after the target disappears, and until then an instant query still returns its last value. The dashboard's *Scrape targets UP* can therefore briefly read 3 during a replacement. Prometheus configuration was never touched.

Wait about 40 seconds after the deletion, then confirm the end state and the application:

```bash
python3 kubernetes/tools/promql.py 'up{job="ticket-service"}'
kubectl -n cloudops-bridge exec -i deploy/bridge -- python - < kubernetes/tools/smoke_test.py
```

**What this proves:** monitoring follows the Deployment automatically. The new pod is discovered and scraped without config changes, the old pod's series end with a staleness marker, and the dashboard keeps receiving data from 2 targets.

### Test B: an unhealthy target shows up=0

This freezes one ticket-service process with `SIGSTOP` from the kind node (the Phase 3 liveness technique; see [../README.md](../README.md#demo-a-liveness-probe-restarts-a-hung-process) for why it must come from the node).

```bash
POD=$(kubectl -n cloudops-bridge get pods -l app.kubernetes.io/name=ticket-service -o jsonpath='{.items[0].metadata.name}')
CID=$(kubectl -n cloudops-bridge get pod $POD -o jsonpath='{.status.containerStatuses[0].containerID}' | sed 's|containerd://||')
HOSTPID=$(docker exec cloudops-bridge-control-plane crictl inspect --output go-template --template '{{.info.pid}}' $CID)
echo $POD $HOSTPID
docker exec cloudops-bridge-control-plane kill -STOP $HOSTPID
```

Repeat this every few seconds for about 70 seconds:

```bash
kubectl -n cloudops-bridge get pod $POD
python3 kubernetes/tools/promql.py --targets
```

Measured timeline from one run:

| After | Kubernetes | Prometheus |
|---|---|---|
| ~7 s | still `1/1` | **`DOWN`**, `error=... context deadline exceeded` (5 s scrape timeout) |
| ~13 s | `0/1` (readiness failed twice) | `DOWN` |
| ~47 s | `RESTARTS 1` (liveness restarted the container) | `DOWN`, error becomes `dial tcp ... connection refused` |
| ~54 s | **`1/1` again** | **still `DOWN`**: no scrape has happened since the restart |
| ~64 s | `1/1` | `UP` |

A second run showed the opposite order at the start: Kubernetes went `0/1` at ~17 s, Prometheus `DOWN` at ~25 s. **Which signal reacts first depends on where each 5 s probe and 15 s scrape falls when the freeze begins.** Both always caught it within one cycle of their own. The pod keeps its name, so the target is the same series. After recovery that pod's `tickets_available` is back to 5000 and `tickets_sold_total` to 0: the in-memory reset, now visible in monitoring.

```bash
python3 kubernetes/tools/promql.py 'tickets_available{job="ticket-service"}'
kubectl -n cloudops-bridge exec -i deploy/bridge -- python - < kubernetes/tools/smoke_test.py
```

**Readiness vs scrape health:** they're independent signals that answer different questions.

| | Kubernetes readiness | Prometheus `up` |
|---|---|---|
| Who checks | kubelet, on the node | Prometheus, over the pod network |
| What | `GET /ready` every 5 s, 2 failures | `GET /metrics` every 15 s, 5 s timeout, a single failure |
| Effect | Removes the pod from Service routing | Records `up=0`. Only observation; changes nothing |
| Uses the Service? | It *drives* the Service endpoints | No: scrapes the pod IP directly, ready or not |

So a NotReady pod is still scraped, and a just-recovered pod can be Ready while `up` is still 0 until the next scrape. In a later phase, alert rules on `up == 0` will turn this signal into an alert. That's why Prometheus must observe pods independently of the Service.

## 7. Resources

```bash
kubectl top pods -n monitoring
```

kind doesn't include metrics-server, so this prints `error: Metrics API not available`. I didn't install metrics-server just for this phase, because the HPA phase will need it and install it properly. Meanwhile, read each container's memory straight from its cgroup:

```bash
kubectl -n monitoring exec deploy/prometheus -- cat /sys/fs/cgroup/memory.current
kubectl -n monitoring exec deploy/grafana -- grep -E '^(anon|file) ' /sys/fs/cgroup/memory.stat
python3 kubernetes/tools/promql.py 'process_resident_memory_bytes{job="prometheus"} / 1024 / 1024'
```

| | Requests | Limits | Measured |
|---|---|---|---|
| Prometheus | 50m CPU, 128Mi | 500m, 512Mi | ~90 MiB RSS, ~870 series |
| Grafana | 50m CPU, 256Mi | 500m, 512Mi | ~187 MiB process memory (`anon`), plus reclaimable file cache |

Grafana's `memory.current` can read close to its limit because it includes file cache. The kernel reclaims that cache before it would OOM-kill. The figure that matters is `anon`.

## 8. Security decisions

Nothing was copied blindly from the app containers. Each setting was tested against the official image.

| Setting | Prometheus | Grafana | Why |
|---|---|---|---|
| `runAsNonRoot` + numeric user | 65534 | 472 | The Prometheus image's user is the *name* `nobody`. `runAsNonRoot` can't verify a name, so the numeric UID must be explicit or the pod won't start |
| `readOnlyRootFilesystem` | yes, with `/prometheus` an emptyDir | yes, with `/var/lib/grafana` and `/tmp` emptyDirs, logs to console | Tested: those are the only paths each image writes |
| `GF_PLUGINS_PREINSTALL_DISABLED` | — | `true` | **Found live:** with a read-only root, Grafana 13's background installer unregistered the bundled Prometheus plugin and failed to replace it, so every query returned `Plugin not registered`. Disabling it also pins plugins to the image's own versions. A test guards this |
| `supplementalGroupsPolicy: Strict` | not needed | yes | The Grafana image puts its user in group 0 (root). Strict drops image-defined groups: `id` went from `groups=0(root),472` to `groups=472` |
| `allowPrivilegeEscalation: false`, `capabilities.drop: [ALL]`, `seccompProfile: RuntimeDefault` | yes | yes | Verified in `/proc/1/status`: `CapEff 0`, `NoNewPrivs 1`, `Seccomp 2` |
| Analytics, update checks, news feed | — | disabled | No outbound calls from a local demo |
| RBAC | namespaced Role, read pods only | none needed | Least privilege |

## 9. Ephemeral data vs declarative configuration

- **Ephemeral (lost on purpose):** Prometheus's time-series database and Grafana's SQLite database (sessions, preferences) live in `emptyDir` volumes. Deleting the pod or the cluster erases history. Retention is 6 hours. No PersistentVolumes are used.
- **Declarative (always restored from Git):** the scrape configuration, discovery rules, RBAC, data source, dashboard provider and dashboard JSON. Recreate the cluster and you get the identical setup with zero clicks. The only generated state is the random Grafana password, created by the documented command.

Editing the dashboard in the UI can't be saved (`allowUiUpdates: false`). To change it, edit `dashboards/ticket-service-overview.json`, then:

```bash
./kubernetes/generate-configmaps.sh
kubectl apply -R -f kubernetes/
kubectl -n monitoring rollout restart deployment/grafana
```

Changing `prometheus.yml` needs `kubectl -n monitoring rollout restart deployment/prometheus`, which also clears its history.

## 10. Alerting (Phase 5)

> Phase 5 built detection and alert delivery to Alertmanager. **Phase 6 (section 11) added the Bridge webhook**: ticket-api alerts are now sent to CloudOps Bridge and enriched. There's still no email, chat or paging.

### Concepts

- **Metrics vs alerts.** A metric is a measurement over time (`up`, `http_requests_total`). An alert is a *condition on* metrics, written in PromQL, that Prometheus evaluates on a schedule. It becomes an alert when the condition is true.
- **Prometheus vs Alertmanager.** Prometheus *decides* whether something is wrong: it evaluates rules every 15 s (`evaluation_interval`). Alertmanager *handles what happens next*: it deduplicates, groups, silences, and routes alerts to receivers, and it tracks each alert until Prometheus says it's resolved. Grafana only visualizes.
- **Lifecycle.** **inactive** (condition false) → **pending** (condition true, but not yet for the whole `for` duration) → **firing** (true for at least `for`; sent to Alertmanager) → **resolved** (condition false again; Prometheus tells Alertmanager, which drops it from the active list). If the condition clears while pending, the alert goes straight back to inactive and never fires.

### The two rules

Defined in `prometheus/rules-configmap.yaml`, loaded from `/etc/prometheus-rules/*.yml`, and evaluated every 15 s.

| Alert | Expression | `for` | Severity | Meaning |
|---|---|---|---|---|
| `TicketServiceTargetDown` | `up{job="ticket-service"} == 0` | 15s | warning | Prometheus can't scrape a ticket-service pod: it's hung, restarting, or unreachable. One alert per pod (keeps `pod` and `instance`). Prometheus's own target is excluded |
| `HighErrorRate` | 5xx ÷ all app requests over 2m `> 0.05` **and** app traffic `> 0.1` req/s | 1m | critical | The service itself is failing requests |

**HighErrorRate in detail.**
- **Numerator:** rate of **5xx** responses.
- **Denominator:** rate of all application responses. Both sides exclude `/health`, `/ready` and `/metrics`, the same as the dashboard.
- **Why not 4xx:** 4xx means the service *correctly rejected* a request (422 validation, 409 sold out, 404 unknown URL). That's normal client behavior, not a service failure, so 4xx never counts.
- **Edge cases:**
  - **No 5xx ever:** the numerator is empty, so no alert.
  - **No traffic:** 0/0 is NaN, the comparison is false, so no alert.
  - **Near-zero traffic:** the `> 0.1 req/s` guard stops one failed request at idle from reading as a 100% outage.
- **Window and `for`:** a 2-minute rate window smooths single bad scrapes, and `for: 1m` requires the condition to hold for a minute. Because of the 2-minute window, the alert clears up to about 2 minutes *after* errors stop.

**These are demo values, not production values.**
- **HighErrorRate:** production thresholds come from the service's SLO, typically multi-window *error-budget burn-rate* alerts tuned against real baseline error rates.
- **TicketServiceTargetDown:** `for: 15s` exists only because Kubernetes restarts a hung pod within ~45 seconds here. A production-style `for: 2–5m` would never fire for a pod that heals itself, which is exactly what production wants, since a self-healing blip shouldn't page anyone. The 15s value also has a real false-positive risk (see Observations).

### Label contract (used by the Bridge, section 11)

Every alert carries stable identity that matches `service-catalog/ticket-api.yaml`:

| Label | Value | Source of truth |
|---|---|---|
| `alertname` | `HighErrorRate`, `TicketServiceTargetDown` | Catalog `alerts:` keys (both are defined) |
| `service` | `ticket-api` | Catalog `service` |
| `environment` | `production` | One of the catalog's `environments` |
| `severity` | `critical` / `warning` | Matches the catalog's production severity for each alert (a test enforces it) |

`TicketServiceTargetDown` also carries `pod`, `instance`, `namespace` and `job`. Its `summary` and `description` annotations name the failing pod.

**Gap closed in Phase 6:** `TicketServiceTargetDown` was missing from the catalog in Phase 5. It now has an entry (first responder CloudOps, suggested checks) and its own runbook, `runbooks/ticket-service-target-down.md`. `HighLatency` has **no rule**, because the app exports no latency histogram or summary. A latency alert would have no real data to evaluate.

### Alertmanager routing

```
route:   receiver no-notifications, group_by [alertname, service, environment]
         group_wait 10s, group_interval 1m, repeat_interval 4h
receivers: no-notifications  (no integrations)
         + Phase 6: child route service="ticket-api" -> cloudops-bridge webhook (section 11)
```

- **`group_by`:** pods failing for the same reason, on the same service, become one group, not one notification each.
- **`group_wait: 10s`** (default 30s): how long a new group waits to collect related alerts before its *first notification*. It does **not** delay the alert appearing in Alertmanager; measured, the alert was visible within ~1 s of Prometheus firing it.
- **`group_interval: 1m`** (default 5m): the minimum gap between notifications about changes to the same group.
- **`repeat_interval: 4h`** (the default): how often a still-firing group is re-sent.
- **`no-notifications`:** a receiver with no integrations is valid configuration; it remains the default for anything that isn't ticket-api. Phase 6 added the `cloudops-bridge` webhook receiver (section 11).

Prometheus finds Alertmanager through DNS: `alerting.alertmanagers` targets `alertmanager.monitoring.svc:9093`.

**Alertmanager deployment:** pinned `prom/alertmanager:v0.34.1`, 1 replica with `Recreate`, HA clustering disabled (`--cluster.listen-address=`, so it doesn't look for peers). UID 65534 is set explicitly, because the image user is the *name* `nobody`. Read-only root filesystem, with an `emptyDir` for `/alertmanager` (silences and notification log; ephemeral). Drop ALL capabilities, no privilege escalation, `RuntimeDefault` seccomp. Requests 10m/32Mi, limits 200m/128Mi; measured ~12 MiB.

### Validate the configuration

`validate-alerting.sh` extracts the exact ConfigMap contents and runs the real tools, using the same pinned images as the cluster (Docker required; activate the venv first, because extraction uses PyYAML):

```bash
source .venv/bin/activate
./kubernetes/monitoring/validate-alerting.sh
```

Expected: `promtool check config` reports `SUCCESS` with `1 rule files found`; `check rules` finds `2 rules`; `test rules` reports `SUCCESS`; `amtool check-config` reports `SUCCESS` with `1 receivers`.

The unit tests in `tests/prometheus/ticket-service-alerts.test.yml` feed **synthetic** series to the real rules:
- TargetDown pending → firing → resolved, and never for `job="prometheus"`.
- HighErrorRate firing at 10% 5xx.
- **No** alert at 20% 4xx, with no traffic, with failing probes only, or at near-zero traffic.

They check rule *logic*; they aren't the live demonstration. As a sanity check, changing the rule to count 4xx or dropping its `job` filter makes these tests fail.

Inside the running Prometheus, against the files it actually mounted:

```bash
kubectl -n monitoring exec deploy/prometheus -- promtool check config /etc/prometheus/prometheus.yml
```

### Access and inspect

Run each port-forward in its own tab (Prometheus from section 2, plus Alertmanager):

```bash
kubectl -n monitoring port-forward svc/alertmanager 19093:9093
```

The Alertmanager UI is at http://localhost:19093, and Prometheus alerts are at http://localhost:19090/alerts. From the terminal:

```bash
python3 kubernetes/tools/promql.py --rules
curl -s localhost:19090/api/v1/alertmanagers
curl -s 'localhost:19093/api/v2/alerts?active=true'
kubectl -n monitoring exec deploy/alertmanager -- amtool alert query --alertmanager.url=http://localhost:9093
kubectl -n monitoring exec deploy/alertmanager -- amtool config routes show --alertmanager.url=http://localhost:9093
```

Expected baseline:
- `--rules` lists both rules with `state=inactive health=ok`, their expressions and their labels.
- `alertmanagers` shows one active URL, `http://alertmanager.monitoring.svc:9093/api/v2/alerts`.
- Alertmanager returns `[]`, and `amtool` prints only its header row.

### Demo A: TicketServiceTargetDown, end to end

> Deliberately freezes one ticket-service process. It's the same safe technique as Phase 3 Demo A, and Kubernetes recovers on its own.

With the Prometheus and Alertmanager port-forwards running:

```bash
POD=$(kubectl -n cloudops-bridge get pods -l app.kubernetes.io/name=ticket-service -o jsonpath='{.items[0].metadata.name}')
CID=$(kubectl -n cloudops-bridge get pod $POD -o jsonpath='{.status.containerStatuses[0].containerID}' | sed 's|containerd://||')
HOSTPID=$(docker exec cloudops-bridge-control-plane crictl inspect --output go-template --template '{{.info.pid}}' $CID)
echo $POD $HOSTPID
```

In a second tab, start the timeline. It prints one line every 3 seconds for 2 minutes. Pass the pod name printed above:

```bash
python3 kubernetes/tools/alert_watch.py 120 POD_NAME
```

Back in the first tab, freeze it:

```bash
docker exec cloudops-bridge-control-plane kill -STOP $HOSTPID
```

While it's firing, look at the alert as Alertmanager has it:

```bash
curl -s 'localhost:19093/api/v2/alerts?active=true' | python3 -m json.tool
```

**Observed, three runs** (seconds after the freeze; run 3 on a cluster rebuilt from scratch, using exactly these commands):

| Event | Run 1 | Run 2 | Run 3 |
|---|---|---|---|
| Kubernetes `0/1` (readiness) | +12 | +9 | +13 |
| Prometheus `up=0` | +18 | +9 | +13 |
| **PENDING** | +27 | +19 | +28 |
| **FIRING**, and **in Alertmanager** | +40 | +34 (Alertmanager had it ~1 s later) | +41 |
| Kubernetes restart (`r=1`) | +40 | +40 | +41 (before FIRING was observed) |
| `up=1` | +43 | +50 | +53 |
| **Resolved** in Prometheus and **cleared** in Alertmanager | +56 | +50 | +56 |

The Alertmanager record held `alertname=TicketServiceTargetDown`, `service=ticket-api`, `environment=production`, `severity=warning`, `pod`, `instance`, `namespace`, `job`, the summary and description, `state: active`, and `receivers: no-notifications`. Prometheus's own history (`ALERTS{alertname="TicketServiceTargetDown"}[5m]`) holds one `pending` and one `firing` sample per run: each state lasted exactly one 15 s evaluation. In run 3, Kubernetes had already restarted the container when the alert fired; Prometheus hadn't scraped the new container yet, so `up` was still 0. That's another example of the two signals moving independently.

**Why it can also not fire.** `up=0` lasted only ~25–41 s before the restarted container was scraped successfully. The alert fires on the second evaluation that sees `up=0`, so if a scrape and a restart line up unluckily, the alert goes **pending → inactive** without firing. That's correct `for` semantics, not a bug. Repeat the demo if that happens.

### Demo B: 4xx traffic must not fire HighErrorRate

```bash
kubectl -n cloudops-bridge exec -i deploy/bridge -- python - 1500 < kubernetes/tools/generate_traffic.py
```

While it runs (about 150 s), compare the client-error ratio with the rule's server-error ratio:

```bash
python3 kubernetes/tools/promql.py 'sum(rate(http_requests_total{job="ticket-service",path!~"/health|/ready|/metrics",status=~"4.."}[2m])) / sum(rate(http_requests_total{job="ticket-service",path!~"/health|/ready|/metrics"}[2m]))'
python3 kubernetes/tools/promql.py --rules
```

**Observed:** 4xx ratio **7.2–7.5%**, above the 5% threshold, with traffic of 4.5–12 req/s for more than 2.5 minutes. Despite that, HighErrorRate stayed `inactive`, Alertmanager stayed `[]`, and `ALERTS{alertname="HighErrorRate"}` was never recorded. A rule that counted 4xx would have fired.

### HighErrorRate cannot be fired live yet

The ticket service has **no natural, safe way to return 5xx**. Its only 500 path is the handler for *unexpected* exceptions, and no request reaches it: invalid input gets 422, sold out gets 409. The rule is loaded, its logic is covered by the promtool unit tests, and the 4xx negative case was verified live. **A live end-to-end HighErrorRate demonstration needs an approved failure-injection mechanism**, a decision deliberately left open. No failure endpoint was added.

### Observations from testing

- **Default rule evaluation is 1 minute.** The Phase 4 config didn't set `evaluation_interval`, so it's now explicit (15 s).
- **Alertmanager visibility vs notification.** Alerts appear in Alertmanager about 1 s after firing; `group_wait` only delays notifications.
- **`endsAt`.** Prometheus sends firing alerts with `endsAt` = now + 4 minutes and refreshes it on every evaluation. If Prometheus stopped, Alertmanager would auto-resolve after 4 minutes. On recovery, Prometheus sends the resolution explicitly, and the alert left Alertmanager's active list immediately.
- **Readiness vs scrape health** differed in timing again: in run 1 Kubernetes went NotReady 6 s before `up=0`; in run 2 they happened together.
- **False positive during a rolling update (observed once, not reproduced).** In the first Phase 3 regression rollout, `TicketServiceTargetDown` fired for an old, terminating pod. That pod stayed in the Kubernetes API, and so in Prometheus discovery, for ~40 s after deletion, while its server had already stopped accepting connections. That gave `up=0` long enough for the 15 s `for`. Four repeat attempts showed normal termination: old pods left discovery 6–12 s after deletion with no alert. These were two rollouts under traffic, one without, and one right after a freeze-and-restart. The root cause of that one slow termination wasn't identified. The lesson stands: a 15 s `for` is short enough to page on a slow pod shutdown, and a production `for` of several minutes absorbs it. Dropping NotReady or terminating pods from discovery would hide this alert, but it would also hide the frozen-pod failure the alert exists to catch.

### Limitations

- No live HighErrorRate demonstration (see above). No HighLatency rule (no metric).
- Alertmanager state is in an `emptyDir`, so silences are lost when the pod restarts. There's a single replica with no HA.
- No email, chat or paging notifications. Alerts go only to the in-cluster Bridge webhook (section 11).
- The demo `for` values trade realism for observability, as documented above.

## 11. Incident enrichment: Alertmanager → CloudOps Bridge (Phase 6)

### Who does what

| Component | Responsibility |
|---|---|
| **Prometheus** | *Detects* a condition: evaluates rules, decides pending/firing/resolved, attaches stable labels |
| **Alertmanager** | *Delivers* it: groups, deduplicates, times notifications (`group_wait`, `group_interval`), retries, and POSTs to the webhook |
| **CloudOps Bridge** | *Explains* it: maps the alert to the service catalog and returns ownership, first responder, dependencies, endpoints, runbook and checks |
| **Service catalog / runbooks** | The source of truth for that context (`service-catalog/*.yaml`, `runbooks/*.md`), mounted as ConfigMaps |

A **raw alert** says *what* fired: `TicketServiceTargetDown` on pod X. An **enriched incident** adds *what to do about it*: who owns it, who responds first, what it depends on, which runbook to open, and what to check first. **The Bridge doesn't diagnose.** It reports no root cause, impact, deployment correlation, logs or metrics, and uses no AI. Everything comes from the alert's labels and annotations plus the catalog.

### Flow

```
up{job="ticket-service"} == 0 for 15s ─► Prometheus FIRING ─► Alertmanager (route service="ticket-api")
   ─► POST http://bridge.cloudops-bridge.svc:8081/webhooks/alertmanager  (send_resolved: true)
   ─► Bridge: labels alertname/service/environment ─► catalog ─► enriched result + log lines
```

Alertmanager (namespace `monitoring`) reaches the Bridge by its cluster DNS name. The Service load-balances each POST to one of the two Ready Bridge pods, so **read logs from both pods**. `--tail=-1` matters: with a label selector, `kubectl logs` otherwise shows only the last 10 lines per pod, and probe lines push the webhook lines out:

```bash
kubectl -n cloudops-bridge logs -l app.kubernetes.io/name=bridge --prefix --tail=-1 --since=10m | grep 'event='
```

### Routing

```yaml
route:
  receiver: no-notifications            # default: everything else goes nowhere
  routes:
    - matchers: ['service="ticket-api"']  # explicit: only the onboarded service
      receiver: cloudops-bridge
receivers:
  - name: cloudops-bridge
    webhook_configs:
      - url: http://bridge.cloudops-bridge.svc:8081/webhooks/alertmanager
        send_resolved: true               # webhook default, made explicit
```

`group_by`, `group_wait: 10s`, `group_interval: 1m` and `repeat_interval: 4h` are unchanged. Other services stay on `no-notifications` until they're deliberately added to the catalog and the route. There are no email, chat or paging integrations.

### Endpoint and payload

`POST /webhooks/alertmanager` accepts Alertmanager's own webhook format, **version `"4"`**, checked against the v0.34.1 source. Other versions get a 422 instead of being misread.

- **Unknown fields are ignored (`extra="ignore"`).** The payload is an external schema that gains fields over time (`notification_reason`, `routeLabels`). Rejecting them would break the integration on an Alertmanager upgrade. The service catalog does the opposite (`extra="forbid"`), because we own it and want typos caught.
- **Timestamps** (`startsAt`, `endsAt`) are passed through as Alertmanager's strings: nanosecond precision, and Go's zero time `0001-01-01T00:00:00Z` as `endsAt` on firing alerts.
- **`POST /incidents/enrich` is unchanged.** Both endpoints share one catalog lookup function, so they always return identical context. It now also knows `TicketServiceTargetDown`.

### Per-alert processing and outcomes

One notification can carry several alerts, firing and resolved mixed. **Every alert is processed and reported individually.** Nothing is dropped, and the notification-level `status` isn't treated as each alert's status.

Mapping uses only the stable labels `alertname`, `service` and `environment`. It never uses pod, IP, fingerprint, or summary text.

| Outcome | When | Result |
|---|---|---|
| `enriched` | Service exists, environment allowed, alert defined in the catalog | Full `incident`: the same model as `/incidents/enrich` |
| `unmapped` | Service and environment known, but **this alert isn't in the catalog** | `incident: null`, plus a reason stating the **operational-readiness gap** (no owner response, checks or runbook). Nothing is invented |
| `rejected` | A required label is missing, the service is unknown, or the environment isn't allowed | `incident: null`, plus the reason |

Every result also carries `status`, `fingerprint`, `starts_at`/`ends_at`, the Prometheus `severity` label, and `source` (the real `pod`, `instance`, `namespace`, summary, description and generator URL).

### Status codes (Alertmanager retries 5xx, never 4xx; verified in its `Retrier.Check`)

| Situation | Response | Why |
|---|---|---|
| Valid payload, any mix of outcomes | **200** | `unmapped`/`rejected` are permanent catalog facts. A 4xx would fail the whole batch, including its valid alerts; a 5xx would make Alertmanager retry something retrying can't fix. Each problem is in the body and logged as a WARNING |
| Malformed JSON, wrong types, unsupported `version` | **422** | Retrying the same bytes can't succeed. Logged as `event=webhook_rejected`, and visible in `alertmanager_notifications_failed_total` |
| Catalog not loaded | **503** | Temporary, so Alertmanager retries |
| Unexpected exception | **500** without a stack trace | Retried |

### Logging

These are single-line `key=value` records on stdout. Raw payloads and annotation text are never logged.

```
event=webhook_received receiver=cloudops-bridge group_status=firing alerts=1 truncated=0
event=alert_processed status=firing outcome=enriched alertname=TicketServiceTargetDown service=ticket-api environment=production severity=warning pod=... instance=... runbook=ticket-service-target-down.md fingerprint=... reason=-
event=incident_context fingerprint=... severity=warning first_responder=cloudops application_owner="Ticket Development" cloudops_owner=CloudOps dependencies=postgresql health=/health readiness=/ready runbook=ticket-service-target-down.md checks=6
```

`incident_context` exists because **Alertmanager discards webhook response bodies**. Without it, the enrichment produced for a real alert would leave no trace. `unmapped`/`rejected` lines are logged at WARNING.

**No persistence.** The Bridge stores nothing. The HTTP response and these log lines are the only record; there's no incident history and no "closing" an incident. A firing event and its resolution share the same `fingerprint`, which is how to correlate them in the logs.

### Trust boundary

The webhook has **no authentication**. The Bridge is ClusterIP-only (no Ingress, no NodePort) on a local, single-tenant cluster with no NetworkPolicy, so **any pod in the cluster could POST forged alerts**. That's acceptable for this demo and **not production-ready**. Production would restrict senders with a NetworkPolicy (only Alertmanager may reach the Bridge), service identity / mTLS, or webhook authentication (`http_config.authorization` with a token from a Secret).

### Demo: a real alert, end to end

> Uses the same controlled freeze as Demo A in section 10. **Nothing is POSTed by hand**: the webhook is Alertmanager's own. Keep the Prometheus and Alertmanager port-forwards running.

Tab 2, Bridge logs from both pods, live:

```bash
kubectl -n cloudops-bridge logs -f -l app.kubernetes.io/name=bridge --prefix --since=1s | grep -E 'event=|POST /webhooks'
```

Tab 3, the alert timeline. Pass the pod name printed in Tab 1:

```bash
python3 kubernetes/tools/alert_watch.py 180 POD_NAME
```

Tab 1, the same commands as Demo A: pick a pod, then freeze it. Later, check Alertmanager's own delivery counter:

```bash
POD=$(kubectl -n cloudops-bridge get pods -l app.kubernetes.io/name=ticket-service -o jsonpath='{.items[0].metadata.name}')
CID=$(kubectl -n cloudops-bridge get pod $POD -o jsonpath='{.status.containerStatuses[0].containerID}' | sed 's|containerd://||')
HOSTPID=$(docker exec cloudops-bridge-control-plane crictl inspect --output go-template --template '{{.info.pid}}' $CID)
echo $POD $HOSTPID
docker exec cloudops-bridge-control-plane kill -STOP $HOSTPID
```

```bash
curl -s localhost:19093/metrics | grep -E '^alertmanager_notifications_(total|failed_total)\{.*webhook'
```

**Observed, three runs** (seconds after the freeze; run 3 on a cluster rebuilt from scratch, using exactly these commands):

| Event | Run 1 | Run 2 | Run 3 |
|---|---|---|---|
| `up=0` | +13 | +16 (Kubernetes `0/1` first, at +10) | +16 (Kubernetes `0/1` at +12) |
| PENDING | +22 | +19 | +25 |
| FIRING (Prometheus) and active in Alertmanager | +38 | +35 | +41 |
| Kubernetes restart (`r=1`) | +44 | +41 | +47 |
| **Bridge receives the firing webhook**, `outcome=enriched` | **+47** | **+43** | **+49** |
| `up=1` | +53 | +56 | +56 |
| Resolved in Prometheus and gone from Alertmanager's active list | +53 | +66 | +72 |
| **Bridge receives the resolved webhook** | **+107** | **+103** | **+109** |
| Webhook deliveries / failures (Alertmanager counter) | 2 / 0 | 2 / 0 | 2 / 0 |

In all three runs:
- **The firing webhook arrived ~9 s after Alertmanager first had the alert.** That's `group_wait: 10s`. The alert was still firing, because the restart doesn't clear `up=0` until the next successful scrape.
- **The resolved webhook arrived exactly 60 s after the firing one** (21:15:40.489 → 21:16:40.486 in run 1), which is `group_interval: 1m`. That's 54 s, 37 s and 37 s after Prometheus had already resolved the alert.
- The request source was the Alertmanager pod's IP; the firing and resolved events shared one fingerprint; and run 2 logged the full context: `first_responder=cloudops`, owners, `dependencies=postgresql`, endpoints, runbook, and `checks=6`.

**Real batching and a duplicate resolved delivery.** Run 2's resolved webhook carried **2 alerts**: run 2's pod, **and run 1's pod**, which had already been delivered as resolved 4 minutes earlier. Prometheus keeps re-sending a resolved alert to Alertmanager for 15 minutes (`resolvedRetention`, in Prometheus's `rules/alerting.go`). Run 1's alert was therefore still in Alertmanager when run 2 created a group with the same `alertname/service/environment`, and it was flushed again. So:
- **duplicate resolved deliveries are normal**, and the fingerprint identifies them;
- **a notification's status is not each alert's status**, which is why the Bridge processes alerts one by one.

**Timing risk.** The firing webhook depends on the alert still firing when `group_wait` expires. In these runs it fired ~9 s after Alertmanager received it, while `up=0` lasted until the first scrape after the restart. If the restarted pod were scraped before `group_wait` expired, Alertmanager would send only a resolved notification. That didn't happen in 3/3 runs, and `group_wait` stays 10 s.

### Limitations

- **No persistence:** no incident history, deduplication or acknowledgement; logs and responses only.
- **No webhook authentication** (see Trust boundary).
- **Only `ticket-api` is routed to the Bridge.** Adding a service means adding its catalog file *and* a route.
- The **catalog is read at startup**: after changing it, regenerate the ConfigMaps and `rollout restart deployment/bridge`. After changing Bridge *code*, rebuild the image, `kind load docker-image` it, and restart.
- **HighErrorRate has never fired live** (no safe 5xx source); it's checked only with isolated payload tests.
- **The rollout false positive from Phase 5 would now reach the Bridge** as a `TicketServiceTargetDown` incident. The runbook explains how to recognize it.

## 12. Troubleshooting

| Symptom | Check |
|---|---|
| A ticket-service target is missing | `kubectl -n cloudops-bridge get pods --show-labels`: the pod label must be `app.kubernetes.io/name=ticket-service` and the container port must be named `http`. Then `kubectl -n monitoring logs deploy/prometheus \| grep -i forbidden` for RBAC problems |
| Target `DOWN` | The `error=` text from `promql.py --targets`. `context deadline exceeded` means the process is hung or slow; `connection refused` means it isn't listening (restarting). Then `kubectl -n cloudops-bridge describe pod POD` |
| Panels say "No data" | Time range (top right) too old or too new? Rates are 0 a minute after traffic stops. Check that the data source health command above says *Successfully queried* |
| Grafana errors `Plugin not registered` | `kubectl -n monitoring logs deploy/grafana \| grep -i 'Failed to install plugin'`. `GF_PLUGINS_PREINSTALL_DISABLED` must be `true` while the root filesystem is read-only |
| Grafana pod in `CreateContainerConfigError` | The `grafana-admin` Secret is missing. Run the create command from section 1 |
| `--rules` shows no rule group | `kubectl -n monitoring get configmap prometheus-rules`, then `kubectl -n monitoring exec deploy/prometheus -- ls /etc/prometheus-rules` and the `promtool check config` command in section 10. Rule changes need `kubectl -n monitoring rollout restart deployment/prometheus` |
| Alert FIRING in Prometheus but not in Alertmanager | `curl -s localhost:19090/api/v1/alertmanagers` must list one active URL; `python3 kubernetes/tools/promql.py 'prometheus_notifications_errors_total'` should stay 0; `kubectl -n monitoring logs deploy/alertmanager` |
| Bridge never logs `event=webhook_received` | `kubectl -n monitoring exec deploy/alertmanager -- amtool config routes show --alertmanager.url=http://localhost:9093` must show `{service="ticket-api"} receiver: cloudops-bridge`. Check the counters `curl -s localhost:19093/metrics \| grep notifications_failed_total` (`serverError` = Bridge 5xx, `clientError` = 4xx) and `kubectl -n monitoring logs deploy/alertmanager`. Remember the firing webhook waits `group_wait` (10 s) and the resolved one waits up to `group_interval` (1 min) |
| Bridge logs `outcome=unmapped` | The alert fired but has no catalog entry: an operational-readiness gap. Add it to `service-catalog/<service>.yaml` (and a runbook), regenerate the ConfigMaps, and restart the Bridge |
| Bridge logs `event=webhook_rejected` | Alertmanager sent something the Bridge couldn't parse (for example a new payload `version`). It isn't retried |
| The same resolved alert logged twice | Expected: Prometheus re-sends resolved alerts for 15 min, and Alertmanager can include them in a later notification for the same group. Match by `fingerprint` |
| Target-down demo went pending → inactive without firing | The pod restarted before the `for` was satisfied (see Demo A). Run it again |
| port-forward: `address already in use` | Another project uses that port. Pick a different local port, for example `29090:9090` |

## 13. Cleanup

Remove only monitoring (the Secret goes with the namespace):

```bash
kubectl delete namespace monitoring
kubectl -n cloudops-bridge delete role,rolebinding prometheus-discovery
```

Or remove everything:

```bash
kind delete cluster --name cloudops-bridge
```
