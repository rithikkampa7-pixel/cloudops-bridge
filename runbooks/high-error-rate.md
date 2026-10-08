# High Error Rate

**Alert:** `HighErrorRate` · **Service:** `ticket-api` · **First responder:** CloudOps
**Owners:** Ticket Development (application), CloudOps (platform)

> Phase 1 note: the service runs locally with uvicorn. Commands for systems
> marked *(future)* apply after later phases add Kubernetes and Prometheus.

## Symptoms

- A large share of requests return HTTP 5xx (for example, more than 5% over 5 minutes).
- Customers report failed ticket purchases or pages that won't load.
- `http_requests_total{status=~"5.."}` rises on `GET /metrics`.

## Initial Checks

Do these first, in this order. Spend about 5 minutes here before going deeper.

1. **Is it up?** Run `GET /health` and `GET /ready`. If either fails, the problem is the process itself.
2. **Is it everything or one endpoint?** Compare 5xx counts per `path` in `/metrics`.
3. **Did something change?** Check for a deployment or config change in the last hour. This is the most common cause.
4. **Is a dependency down?** Check database connectivity.

## Useful Commands

```bash
# Health and readiness
curl -s http://localhost:8080/health
curl -s http://localhost:8080/ready

# Error counts by endpoint and status
curl -s http://localhost:8080/metrics | grep http_requests_total

# Get incident context (owners, dependencies, checks)
curl -s -X POST http://localhost:8081/incidents/enrich \
  -H 'Content-Type: application/json' \
  -d '{"service":"ticket-api","environment":"production","alert":"HighErrorRate"}'

# (future) Kubernetes
kubectl -n ticket get pods
kubectl -n ticket logs deploy/ticket-api --since=15m | grep -i error
kubectl -n ticket rollout history deploy/ticket-api
```

## Common Causes

| Cause | Signal |
|---|---|
| Bad deployment | Errors start right after a release |
| Database unavailable or slow | Errors on write paths (`/tickets/purchase`) only |
| Resource exhaustion (CPU/memory) | Errors appear with high traffic; pods restarting *(future)* |
| Misconfiguration | Errors start after a config or secret change |

## Recovery

- **Deployment-related:** roll back first and investigate after (see below).
- **Dependency down:** restore the dependency. Escalate to its owner if it isn't yours.
- **Overloaded:** scale out (*future:* HPA or `kubectl scale`).
- **Process unhealthy:** restart the service.

## Rollback

```bash
# (future) Kubernetes
kubectl -n ticket rollout undo deploy/ticket-api
kubectl -n ticket rollout status deploy/ticket-api
```

Locally: check out the previous known-good commit and restart uvicorn.

## Escalation

- **Development (Ticket Development):** the cause is application code or business logic, such as new exceptions after a release that a rollback doesn't fix.
- **CloudOps lead:** the platform, network, or a shared dependency is down, or there's no improvement after 30 minutes.
- Always include the enriched incident output and the time errors started.

## Verification

- 5xx rate is back to baseline for at least 10 minutes.
- `/health` and `/ready` return 200.
- A test purchase succeeds: `POST /tickets/purchase` with `{"quantity": 1}`.
- **Afterward:** add anything new you learned to this runbook.
