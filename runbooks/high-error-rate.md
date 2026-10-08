# High Error Rate

**Alert:** `HighErrorRate` · **Service:** `ticket-api` · **First responder:** CloudOps
**Owners:** Ticket Development (application), CloudOps (platform)

> Environment: local kind cluster `cloudops-bridge`, namespace `cloudops-bridge`, Deployment `ticket-service`. Commands below assume `kubectl` points at `kind-cloudops-bridge`. Command blocks contain no `#` comments, so they paste safely into zsh.

## Symptoms

- A large share of requests return HTTP 5xx (for example, more than 5% over 5 minutes).
- Customers report failed ticket purchases or pages that won't load.
- `http_requests_total{status=~"5.."}` rises on `GET /metrics`.

## Initial Checks

Do these first, in this order. Spend about 5 minutes here before going deeper.

1. **Are the pods up and Ready?** If a pod is not Ready or keeps restarting, the problem is the process or its probes.
2. **Is it everything or one endpoint?** Compare 5xx counts per `path` in `/metrics`.
3. **Did something change?** Check rollout history for a deployment in the last hour. This is the most common cause.
4. **Is a dependency down?** Check database connectivity.

## Useful Commands

Pod state, restart counts, and recent cluster events (newest last):

```bash
kubectl -n cloudops-bridge get pods -l app.kubernetes.io/name=ticket-service -o wide
kubectl -n cloudops-bridge get events --sort-by=.lastTimestamp
```

Why a specific pod is unhealthy (probe failures, OOMKilled, image errors appear under Events and Last State):

```bash
kubectl -n cloudops-bridge describe pod POD_NAME
```

Application logs. `--previous` shows the container that ran *before the last restart*. It only works if `RESTARTS` is above 0; otherwise it returns `previous terminated container ... not found`:

```bash
kubectl -n cloudops-bridge logs deploy/ticket-service --since=15m
kubectl -n cloudops-bridge logs POD_NAME --previous
```

Error counts by endpoint and status (run the port-forward in its own tab):

```bash
kubectl -n cloudops-bridge port-forward svc/ticket-service 8080:8080
```

```bash
curl -s localhost:8080/health
curl -s localhost:8080/ready
curl -s localhost:8080/metrics | grep http_requests_total
```

Incident context (owners, dependencies, checks), with the bridge port-forwarded to 8081:

```bash
curl -s -X POST localhost:8081/incidents/enrich -H 'Content-Type: application/json' -d '{"service":"ticket-api","environment":"production","alert":"HighErrorRate"}'
```

Recent deployments:

```bash
kubectl -n cloudops-bridge rollout history deployment/ticket-service
```

## Common Causes

| Cause | Signal |
|---|---|
| Bad deployment | Errors start right after a rollout; new pods not Ready |
| Database unavailable or slow | Errors on write paths (`/tickets/purchase`) only |
| Resource exhaustion (CPU/memory) | Errors with high traffic; `OOMKilled` in `describe pod`; restarts rising |
| Misconfiguration | Errors start after a config change |

## Recovery

- **Deployment-related:** roll back first and investigate after (see below).
- **Dependency down:** restore the dependency. Escalate to its owner if it isn't yours.
- **Single unhealthy pod:** the liveness probe restarts a hung container automatically. If a pod is stuck, delete it and the Deployment replaces it:

```bash
kubectl -n cloudops-bridge delete pod POD_NAME
```

- **Overloaded:** scale out manually (autoscaling arrives in a later phase). Afterward, scale back or re-apply the manifests, because the manifests declare 2 replicas:

```bash
kubectl -n cloudops-bridge scale deployment/ticket-service --replicas=3
```

## Rollback

**Do not run a plain `rollout undo` blindly.** It goes to the *immediately previous* revision. After an earlier rollback, that can be the broken release itself. This happened while testing this runbook: `undo` redeployed a bad image. Check the history, confirm which revision has a known-good image, and roll back to it explicitly:

```bash
kubectl -n cloudops-bridge rollout history deployment/ticket-service
kubectl -n cloudops-bridge rollout history deployment/ticket-service --revision=REVISION
kubectl -n cloudops-bridge rollout undo deployment/ticket-service --to-revision=REVISION
kubectl -n cloudops-bridge rollout status deployment/ticket-service --timeout=120s
```

## Escalation

- **Development (Ticket Development):** the cause is application code or business logic, such as new exceptions after a release that a rollback doesn't fix.
- **CloudOps lead:** the platform, network, or a shared dependency is down, or there's no improvement after 30 minutes.
- Always include the enriched incident output and the time errors started.

## Verification

- 5xx rate is back to baseline for at least 10 minutes.
- `kubectl -n cloudops-bridge get deploy ticket-service` shows `2/2` ready, and restart counts are stable.
- `/health` and `/ready` return 200.
- A test purchase succeeds: `POST /tickets/purchase` with `{"quantity": 1}`.
- **Afterward:** add anything new you learned to this runbook.
