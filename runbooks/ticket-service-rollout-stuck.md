# Ticket Service Rollout Stuck

**Alert:** `TicketServiceRolloutStuck` · **Service:** `ticket-api` · **First responder:** CloudOps
**Owners:** Ticket Development (application), CloudOps (platform)

> Environment: local kind cluster `cloudops-bridge`. Apps run in namespace `cloudops-bridge`; Prometheus and Alertmanager in `monitoring`; Argo CD in `argocd`. Command blocks contain no `#` comments, so they paste safely into zsh. Commands that use `localhost:19090` need the Prometheus port-forward from `kubernetes/monitoring/README.md`.

## What the alert means

The ticket-service Deployment reports `Progressing=False` with reason `ProgressDeadlineExceeded`: a rollout has made no progress for longer than `progressDeadlineSeconds` (120 s in `kubernetes/ticket-service/deployment.yaml`), and the condition has held for a further minute. New pods aren't becoming Ready.

It does **not** mean the service is down:

- The strategy is `maxUnavailable: 0`, so Kubernetes keeps the previous version's pods until new ones are Ready. Users are normally still served by the old version.
- Kubernetes **doesn't roll back on its own**. The stuck rollout stays until the desired state changes.
- A normal rolling update never triggers this alert. It reports `ReplicaSetUpdated` or `NewReplicaSetAvailable`, never `ProgressDeadlineExceeded`.

**Synced doesn't mean Healthy.** ticket-service is deployed by Argo CD from Git. After a bad release, Argo CD shows **Synced** (the cluster matches Git) and **Degraded** (the workload isn't working). Git, not the cluster, is where the problem is.

## 1. Establish what failed

Rollout state and the Deployment's conditions:

```bash
kubectl -n cloudops-bridge rollout status deployment/ticket-service --timeout=10s
kubectl -n cloudops-bridge get deploy ticket-service -o jsonpath='{range .status.conditions[*]}{.type}={.status} {.reason}: {.message}{"\n"}{end}'
```

ReplicaSets: which one is new, and which image each runs:

```bash
kubectl -n cloudops-bridge get rs -l app.kubernetes.io/name=ticket-service -o wide
```

The failing pods and why they're waiting (`ErrImageNeverPull`, `ErrImagePull`, `CrashLoopBackOff`, `Pending`, failing readiness):

```bash
kubectl -n cloudops-bridge get pods -l app.kubernetes.io/name=ticket-service -o wide
kubectl -n cloudops-bridge describe pod POD_NAME
kubectl -n cloudops-bridge get events --sort-by=.lastTimestamp
```

If the reason is an image error, check whether the image exists in the kind node. There's no registry in this project, so every image must be loaded with `kind load docker-image`, and `imagePullPolicy: Never` means it's never pulled:

```bash
docker exec cloudops-bridge-control-plane crictl images | grep ticket-service
```

If the new pods start and then crash, read their logs:

```bash
kubectl -n cloudops-bridge logs POD_NAME --previous
```

Are users still served? The previous version's pods should be the Service's ready endpoints:

```bash
kubectl -n cloudops-bridge get endpointslices -l kubernetes.io/service-name=ticket-service
kubectl -n cloudops-bridge exec -i deploy/bridge -- python - < kubernetes/tools/smoke_test.py
```

What did Argo CD deploy, and what changed? Compare its sync status with its health status, then look at the last Git change to the manifests:

```bash
kubectl -n argocd get application ticket-service -o jsonpath='sync={.status.sync.status} health={.status.health.status} revision={.status.sync.revision}{"\n"}'
git log --oneline -5 -- kubernetes/ticket-service/
git show HEAD -- kubernetes/ticket-service/deployment.yaml
```

## 2. Decide

| What you found | Meaning | Action |
|---|---|---|
| New pods `ErrImageNeverPull` / `ErrImagePull`; the last commit changed the image tag | The release references an image that isn't in the cluster | Recover through Git (below). Or, if the image should exist, build and `kind load` it; the rollout then continues |
| New pods `CrashLoopBackOff` or never Ready | The new version fails at startup or on its readiness check | Recover through Git, then hand the logs to Development |
| New pods `Pending` | Scheduling: not enough CPU or memory on the node | Check `describe pod` events and node capacity. Escalate to the CloudOps lead |
| Old pods are also not Ready | Not just a failed release: the service is affected | Treat it as an outage. Check `TicketServiceTargetDown` and the smoke test |

## 3. Recover through Git

ticket-service is GitOps-managed. **Don't patch or `kubectl apply` the live Deployment** as the normal fix: Argo CD's self-heal reverts manual changes to Git-managed fields, and Git would still contain the bad release. Revert the change that broke the rollout instead:

Push to the branch the Application tracks (`targetRevision` in `gitops/ticket-service-application.yaml`):

```bash
git revert BAD_COMMIT
git push
```

Argo CD deploys the revert when it sees the new commit. To check it now instead of waiting for the next poll, refresh and watch:

```bash
kubectl -n argocd annotate application ticket-service argocd.argoproj.io/refresh=normal --overwrite
kubectl -n cloudops-bridge rollout status deployment/ticket-service --timeout=180s
```

Argo CD doesn't wait for CI ([gitops/README.md](../gitops/README.md#sync-policy)), so check that the revert's GitHub Actions run passes too.

## 4. Escalation

- **CloudOps lead:** pods `Pending` for capacity reasons, old pods also affected, or no recovery 15 minutes after the revert.
- **Development (Ticket Development):** new pods crash with application errors.
- Include the bad commit, the Deployment conditions, the waiting reason of the new pods, and the Argo CD sync and health status.

## 5. Verify recovery

- `kubectl -n cloudops-bridge rollout status deployment/ticket-service` reports `successfully rolled out`, and the Deployment is `2/2` (or the HPA's current count).
- The Progressing condition reason is `NewReplicaSetAvailable`.
- Argo CD shows **Synced** and **Healthy** at the revert commit.
- The alert is gone from Prometheus (`python3 kubernetes/tools/promql.py --rules`) and Alertmanager.
- The smoke test passes.
- Record what failed and what fixed it in this runbook.
