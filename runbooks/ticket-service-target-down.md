# Ticket Service Target Down

**Alert:** `TicketServiceTargetDown` · **Service:** `ticket-api` · **First responder:** CloudOps
**Owners:** Ticket Development (application), CloudOps (platform)

> Environment: local kind cluster `cloudops-bridge`. Apps run in namespace `cloudops-bridge`; Prometheus and Alertmanager in `monitoring`. Command blocks contain no `#` comments, so they paste safely into zsh. Commands that use `localhost:19090` need the Prometheus port-forward from `kubernetes/monitoring/README.md`.

## What the alert means

Prometheus failed to scrape `/metrics` on **one ticket-service pod** for at least 15 seconds (`up{job="ticket-service"} == 0`). The alert labels name the pod (`pod`) and its address (`instance`).

It does **not** mean the service is down:

- The other replica usually keeps serving.
- Prometheus scrapes each pod directly, independent of Kubernetes readiness. The pod can be NotReady, Ready, or already restarted when you look.
- **Kubernetes may already be fixing it.** A hung pod is restarted by its liveness probe within about 45 s. Don't restart anything until you know what failed.

## 1. Establish what failed

Is the pod still there, and has it restarted?

```bash
kubectl -n cloudops-bridge get pods -l app.kubernetes.io/name=ticket-service -o wide
```

What does Prometheus see now, and why did the scrape fail? `context deadline exceeded` means the process is hung or slow; `connection refused` means nothing is listening (starting, stopping, or crashed).

```bash
python3 kubernetes/tools/promql.py --targets
python3 kubernetes/tools/promql.py 'up{job="ticket-service"}'
```

Why did the pod change state? Look for probe failures, `Killing`, OOM, or image errors in the Events, and `Last State` with its exit code (137 = killed):

```bash
kubectl -n cloudops-bridge describe pod POD_NAME
kubectl -n cloudops-bridge get events --sort-by=.lastTimestamp
```

What was the process doing before it stopped? `--previous` works only if `RESTARTS` is above 0:

```bash
kubectl -n cloudops-bridge logs POD_NAME --previous
kubectl -n cloudops-bridge logs POD_NAME --since=15m
```

Are users still served? The Service should list at least one ready endpoint, and the smoke test should pass:

```bash
kubectl -n cloudops-bridge get endpointslices -l kubernetes.io/service-name=ticket-service
kubectl -n cloudops-bridge exec -i deploy/bridge -- python - < kubernetes/tools/smoke_test.py
```

Was a deployment in progress?

```bash
kubectl -n cloudops-bridge rollout status deployment/ticket-service --timeout=10s
kubectl -n cloudops-bridge rollout history deployment/ticket-service
```

## 2. Decide

| What you found | Meaning | Action |
|---|---|---|
| `RESTARTS` increased, pod `1/1`, target `UP` again | A hung or crashed process; liveness restarted it | Don't touch it. Read `logs --previous` for the cause and record it. If it repeats, escalate to Development |
| Pod `0/1` and not recovering, restarts climbing | Crash loop or a dependency problem | Investigate from `describe` and logs. Don't keep restarting it |
| Pod `1/1` Ready, but the target stays `DOWN` | The app serves users but Prometheus can't reach it: a monitoring-path problem | Check the error text, the Prometheus logs, and the network path from the `monitoring` namespace |
| Pod `Terminating`, a rollout in progress, alert clears within a minute | A known false positive: a terminating pod stays a scrape target briefly after it stops accepting connections | Confirm the rollout completed and the alert resolved. No action |
| Pod gone, a replacement `1/1` and `UP` | Replaced (deletion or eviction) | Verify, then find out why it was deleted |

Restarting a pod is a last resort, used only when it's stuck *and* Kubernetes isn't recovering it. The Deployment replaces it:

```bash
kubectl -n cloudops-bridge delete pod POD_NAME
```

## 3. Escalation

- **CloudOps lead:** the target stays `DOWN` while the pod is Ready (monitoring path), both replicas are affected, or there's no recovery after 15 minutes.
- **Development (Ticket Development):** repeated restarts with application errors in `logs --previous`.
- Include the alert labels (`pod`, `instance`), the scrape error text, and `RESTARTS` and events.

## 4. Verify recovery

- `python3 kubernetes/tools/promql.py --targets` shows both ticket-service targets `UP`.
- `kubectl -n cloudops-bridge get deploy ticket-service` shows `2/2`.
- The alert is gone from Prometheus (`python3 kubernetes/tools/promql.py --rules`) and from Alertmanager (`curl -s 'localhost:19093/api/v2/alerts?active=true'` returns `[]`).
- The smoke test passes.
- Record what failed and what fixed it in this runbook.
