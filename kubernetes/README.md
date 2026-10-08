# CloudOps Bridge on Kubernetes (kind)

Phase 3 runs both services on a local, single-node [kind](https://kind.sigs.k8s.io/) cluster. No cloud account, registry, or paid infrastructure is involved.

> Every code block contains only commands (no `#` comments), so blocks paste cleanly into zsh. Run commands from the repository root, `~/cloudops-bridge`.

```
kubernetes/
├── namespace.yaml                     namespace cloudops-bridge
├── ticket-service/deployment.yaml     2 replicas, probes, resources, securityContext
├── ticket-service/service.yaml        ClusterIP :8080
├── bridge/deployment.yaml             same, plus read-only ConfigMap mounts
├── bridge/service.yaml                ClusterIP :8081
├── config/                            GENERATED ConfigMaps (do not edit)
├── monitoring/                        Phase 4: Prometheus + Grafana (see monitoring/README.md)
├── generate-configmaps.sh             regenerates generated ConfigMaps from service-catalog/, runbooks/, dashboards/
└── tools/
    ├── smoke_test.py                  in-cluster check of every endpoint
    ├── check_availability.py          polls a Service during rollouts and counts failures
    ├── generate_traffic.py            deterministic traffic for monitoring checks (Phase 4)
    └── promql.py                      compact PromQL / target queries from your Mac (Phase 4)
```

Monitoring (Prometheus, Grafana, dashboard, PromQL) is documented in **[monitoring/README.md](monitoring/README.md)**.

## 1. Prerequisites

Check that Docker Desktop is running and the tools are installed:

```bash
docker version --format '{{.Server.Version}}'
kind version
kubectl version --client
```

kind v0.33 creates a Kubernetes **1.37** cluster. kubectl should be within one minor version (1.36 or newer). Docker Desktop bundles an older kubectl at `/usr/local/bin/kubectl`. If yours is older, install a current one from Homebrew. It goes in `/opt/homebrew/bin`, which comes first in `PATH`, and leaves Docker Desktop's copy untouched:

```bash
brew install kubernetes-cli
```

## 2. Create the cluster

No kind config file is needed. The default is a single control-plane node, which is all this phase requires.

```bash
kind create cluster --name cloudops-bridge
kubectl config current-context
kubectl wait --for=condition=Ready node --all --timeout=120s
kubectl get nodes
```

Expected: context `kind-cloudops-bridge`, then `node/cloudops-bridge-control-plane condition met`, and one `Ready` node. Waiting matters: if you deploy before the node is Ready, pods briefly show a `FailedScheduling ... untolerated taint` event. That clears on its own, but it's confusing.

`kind create cluster` switches kubectl's current context. To return to another cluster later, use `kubectl config use-context <name>`.

## 3. Build and load the images

The images are built from the existing Phase 2 Dockerfiles and copied straight into the kind node. Nothing is pushed to a registry.

```bash
docker build -t cloudops-bridge-ticket-service:phase3 -f ticket_service/Dockerfile .
docker build -t cloudops-bridge-bridge:phase3 -f bridge/Dockerfile .
kind load docker-image cloudops-bridge-ticket-service:phase3 cloudops-bridge-bridge:phase3 --name cloudops-bridge
```

**Why `imagePullPolicy: Never`:** these images exist only inside the kind node. With `IfNotPresent`, a missing image makes the kubelet try Docker Hub, which fails with a misleading `ErrImagePull`. `Never` fails immediately with `ErrImageNeverPull`, which says exactly what's wrong: the image wasn't loaded. This changes when a registry is introduced.

## 4. Deploy

The namespaces are applied first because `apply -R` processes files alphabetically, and `bridge/` would come before `namespace.yaml`. The second command creates a random Grafana admin password as a Secret, **only if it doesn't exist yet**. It's never stored in Git (see [monitoring/README.md](monitoring/README.md#1-install)). On a fresh cluster it first prints `NotFound`, which is expected.

```bash
kubectl apply -f kubernetes/namespace.yaml -f kubernetes/monitoring/namespace.yaml
kubectl -n monitoring get secret grafana-admin || kubectl -n monitoring create secret generic grafana-admin --from-literal=admin-password="$(openssl rand -base64 24)"
kubectl apply -R -f kubernetes/
kubectl -n cloudops-bridge rollout status deployment/ticket-service --timeout=120s
kubectl -n cloudops-bridge rollout status deployment/bridge --timeout=120s
kubectl -n monitoring rollout status deployment/prometheus --timeout=300s
kubectl -n monitoring rollout status deployment/grafana --timeout=300s
```

The first deploy pulls the Prometheus and Grafana images from Docker Hub, which can take a minute.

## 5. Inspect

```bash
kubectl get namespaces
kubectl -n cloudops-bridge get deployments
kubectl -n cloudops-bridge get pods -o wide
kubectl -n cloudops-bridge get services
kubectl -n cloudops-bridge get configmaps
```

Expected: both Deployments `2/2`, four pods `1/1 Running` with 0 restarts, two `ClusterIP` Services, and ConfigMaps `service-catalog` and `runbooks`. Monitoring runs separately in namespace `monitoring`: `kubectl -n monitoring get pods`.

## 6. Test the APIs

### Through port-forward (from your Mac)

Run each port-forward in its own terminal tab and leave it running:

```bash
kubectl -n cloudops-bridge port-forward svc/ticket-service 8080:8080
```

```bash
kubectl -n cloudops-bridge port-forward svc/bridge 8081:8081
```

Then, in a third tab, use the same requests as the rest of the project:

```bash
curl -s localhost:8080/health
curl -s localhost:8080/ready
curl -s localhost:8080/tickets
curl -s -X POST localhost:8080/tickets/purchase -H 'Content-Type: application/json' -d '{"quantity": 2}'
curl -s localhost:8080/metrics
curl -s localhost:8081/health
curl -s localhost:8081/ready
curl -s localhost:8081/services
curl -s -X POST localhost:8081/incidents/enrich -H 'Content-Type: application/json' -d '{"service":"ticket-api","environment":"production","alert":"HighErrorRate"}'
curl -s -w ' [%{http_code}]\n' -X POST localhost:8081/incidents/enrich -H 'Content-Type: application/json' -d '{"service":"payments","environment":"production","alert":"HighErrorRate"}'
curl -s -w ' [%{http_code}]\n' -X POST localhost:8081/incidents/enrich -H 'Content-Type: application/json' -d '{"service":"ticket-api"}'
```

The last two should end in `[404]` and `[422]`.

**Port-forward limitation:** `port-forward svc/...` attaches to **one** pod chosen when it starts. If that pod is replaced (restart, rollout, deletion), the port-forward breaks with `lost connection to pod`. Stop it with Ctrl+C and start it again. That's why the demos below measure availability from inside the cluster instead.

### From inside the cluster (through the Services)

`smoke_test.py` calls every endpoint through the Service DNS names, the way in-cluster clients do. It uses only the Python standard library, so it runs inside the existing images:

```bash
kubectl -n cloudops-bridge exec -i deploy/bridge -- python - < kubernetes/tools/smoke_test.py
```

Expected: eight `PASS` lines, an `enrich -> severity=critical ...` line, and `ALL PASSED`.

## 7. Verify security and configuration

**Non-root.** Expect `uid=10001(app)` for both, and `Uid: 10001 ...` for PID 1 (the uvicorn process):

```bash
kubectl -n cloudops-bridge exec deploy/ticket-service -- id
kubectl -n cloudops-bridge exec deploy/bridge -- id
kubectl -n cloudops-bridge exec deploy/ticket-service -- grep Uid /proc/1/status
kubectl -n cloudops-bridge exec deploy/bridge -- grep Uid /proc/1/status
```

**securityContext and resources, as Kubernetes stored them:**

```bash
kubectl -n cloudops-bridge get deploy ticket-service -o jsonpath='{.spec.template.spec.securityContext}{"\n"}{.spec.template.spec.containers[0].securityContext}{"\n"}{.spec.template.spec.containers[0].resources}{"\n"}'
```

**What the kernel actually enforces.** Expect `CapEff: 0000000000000000` (no capabilities), `NoNewPrivs: 1`, and `Seccomp: 2` (filter active):

```bash
kubectl -n cloudops-bridge exec deploy/bridge -- grep -E 'CapEff|NoNewPrivs|Seccomp:' /proc/1/status
```

**Read-only root filesystem.** Both commands are *expected to fail* with `Read-only file system`:

```bash
kubectl -n cloudops-bridge exec deploy/ticket-service -- touch /app/test
kubectl -n cloudops-bridge exec deploy/ticket-service -- touch /tmp/test
```

**ConfigMaps mounted and readable.** Expect the two paths, the `ticket-api.yaml` symlink kubelet creates, and the first lines of the runbook:

```bash
kubectl -n cloudops-bridge exec deploy/bridge -- printenv CATALOG_DIR RUNBOOK_DIR
kubectl -n cloudops-bridge exec deploy/bridge -- ls -l /etc/cloudops-bridge/service-catalog /etc/cloudops-bridge/runbooks
kubectl -n cloudops-bridge exec deploy/bridge -- head -3 /etc/cloudops-bridge/runbooks/high-error-rate.md
```

**ConfigMaps are read-only.** The write is *expected to fail*, and both mounts should show `ro` in the kernel's mount table:

```bash
kubectl -n cloudops-bridge exec deploy/bridge -- touch /etc/cloudops-bridge/service-catalog/test
kubectl -n cloudops-bridge exec deploy/bridge -- grep cloudops-bridge /proc/mounts
```

---

## 8. Failure and recovery demonstrations

> **These commands deliberately break things.** They're safe on this local cluster. Each one ends with the application verified again. Run them in order, and wait for each one to recover before starting the next.

Keep a **watch tab** open for all of them:

```bash
kubectl -n cloudops-bridge get pods -w
```

### Demo A: Liveness probe restarts a hung process

**What we break:** we freeze one ticket-service uvicorn process with `SIGSTOP`. It keeps running but stops answering, like a deadlock. Nothing in the application is changed.

**Why from the node, not `kubectl exec`:** inside a container the app is PID 1, and Linux ignores `SIGSTOP` sent to PID 1 from inside its own PID namespace. `kubectl exec ... kill -STOP 1` exits successfully and **does nothing**. We verified that. So we find the process's PID on the kind node and signal it from there.

In your main tab, find the pod and its PID on the node:

```bash
POD=$(kubectl -n cloudops-bridge get pods -l app.kubernetes.io/name=ticket-service -o jsonpath='{.items[0].metadata.name}')
CID=$(kubectl -n cloudops-bridge get pod $POD -o jsonpath='{.status.containerStatuses[0].containerID}' | sed 's|containerd://||')
HOSTPID=$(docker exec cloudops-bridge-control-plane crictl inspect --output go-template --template '{{.info.pid}}' $CID)
echo $POD $HOSTPID
docker exec cloudops-bridge-control-plane ps -o pid,user,stat,cmd -p $HOSTPID
```

Expected: a pod name and a PID owned by user `10001` with state `Ssl` (running).

Freeze it:

```bash
docker exec cloudops-bridge-control-plane kill -STOP $HOSTPID
```

**What to observe** (watch tab, about 45 seconds in total):

| After | What you see | Why |
|---|---|---|
| ~10 s | Pod goes `0/1` | Readiness `/ready` timed out 2× (period 5 s, failureThreshold 2). The pod is removed from the Service, so traffic goes only to the healthy replica |
| ~30 s | Liveness fails | `/health` timed out 3× (period 10 s, failureThreshold 3). kubelet kills the container |
| ~40 s | `RESTARTS` becomes `1` | Same pod, new container. The pod is **not** replaced |
| ~45 s | Pod back to `1/1` | Readiness passes, so it rejoins the Service |

Evidence:

```bash
kubectl -n cloudops-bridge get events --field-selector involvedObject.name=$POD --sort-by=.lastTimestamp
kubectl -n cloudops-bridge describe pod $POD
kubectl -n cloudops-bridge logs $POD --previous
```

Look for `Readiness probe failed ... context deadline exceeded`, `Liveness probe failed`, and `Container ticket-service failed liveness probe, will be restarted`. In `describe`, look for `Restart Count: 1` and `Last State: Terminated` with `Exit Code: 137`. 137 = 128 + 9 (SIGKILL): the frozen process couldn't act on SIGTERM, so kubelet force-killed it after the 15-second grace period. `logs --previous` shows the frozen container's log up to the freeze.

Verify the application:

```bash
kubectl -n cloudops-bridge exec -i deploy/bridge -- python - < kubernetes/tools/smoke_test.py
```

**What this proves:** a process that's running but not working is detected and restarted without human action. **Measured impact:** with an availability check running, 4–5 requests failed per run (out of about 310–350). All of them fell in the ~10 seconds before readiness removed the frozen pod. Readiness limits the damage; it doesn't prevent the first few failures. Shorter probe periods narrow that window at the cost of more probe traffic.

### Demo B: Deployment reconciliation (not a probe)

**What we break:** we delete one pod.

```bash
kubectl -n cloudops-bridge get pods -l app.kubernetes.io/name=ticket-service
kubectl -n cloudops-bridge delete pod $(kubectl -n cloudops-bridge get pods -l app.kubernetes.io/name=ticket-service -o jsonpath='{.items[1].metadata.name}')
kubectl -n cloudops-bridge get pods -l app.kubernetes.io/name=ticket-service
```

**What to observe:** a pod with a **new name** and `RESTARTS 0` appears immediately, `0/1` and then `1/1` after about 8 seconds.

```bash
kubectl -n cloudops-bridge get events --sort-by=.lastTimestamp | grep -E 'SuccessfulCreate|Killing'
kubectl -n cloudops-bridge exec -i deploy/bridge -- python - < kubernetes/tools/smoke_test.py
```

**Why, and how it differs from Demo A:** no probe is involved. The ReplicaSet controller sees 1 pod where the Deployment declares 2, and creates a new pod (`SuccessfulCreate`). In Demo A the *kubelet* restarted a container *inside the same pod* (`failed liveness probe, will be restarted`). Reconciliation handles pods that disappear (node loss, eviction, deletion); liveness handles pods that exist but are broken.

### Demo C: Rolling update with zero dropped requests

**What we change:** `kubectl rollout restart`, which adds a timestamp annotation to the pod template. This is the real way to roll pods after a ConfigMap change, because the bridge reads its catalog only at startup.

In a second tab, start the availability check. It calls ticket-service through its Service 5 times per second for 60 seconds, from inside a bridge pod:

```bash
kubectl -n cloudops-bridge exec -i deploy/bridge -- python - http://ticket-service:8080/tickets 60 < kubernetes/tools/check_availability.py
```

Within a few seconds, in the main tab:

```bash
kubectl -n cloudops-bridge rollout restart deployment/ticket-service
kubectl -n cloudops-bridge annotate deployment/ticket-service kubernetes.io/change-cause="rollout restart" --overwrite
kubectl -n cloudops-bridge rollout status deployment/ticket-service
```

**What to observe** in the watch tab (`maxSurge: 1`, `maxUnavailable: 0`):

1. A new pod appears (`Pending` → `ContainerCreating` → `0/1` → `1/1`) while **both old pods stay `1/1`**.
2. Only after the new pod is Ready does one old pod go `Terminating`.
3. This repeats for the second pod. There are never fewer than 2 Ready pods.

The availability check should end with `... 0 failed over 60s`.

```bash
kubectl -n cloudops-bridge get deployment ticket-service
kubectl -n cloudops-bridge rollout history deployment/ticket-service
kubectl -n cloudops-bridge exec -i deploy/bridge -- python - < kubernetes/tools/smoke_test.py
```

To roll the bridge instead, run the check from a ticket-service pod against `http://bridge:8081/ready` and restart `deployment/bridge`.

**Real finding: the `preStop` hook.** The first version of these manifests **dropped 4 of 198 requests** (`Connection refused`, then `timed out`) during this exact rollout, even though the strategy never went below 2 Ready pods. The cause is that when a pod is deleted, kubelet sends SIGTERM *at the same time* as the pod is removed from the Service endpoints. uvicorn exits in under a second, but kube-proxy updates its routing a moment later, so a few requests reached a process that was already gone. The fix is `lifecycle.preStop.sleep: 5s`, which keeps the old pod serving for 5 more seconds while routing catches up. After the fix: **0 failed** in every run since (242, 242, 290 and 339 requests across rollouts and the rollback). This is why "it never drops below 2 Ready pods" isn't enough on its own to guarantee zero downtime.

### Demo D: Bad release, then rollback

**What we break:** we deploy an image tag that was never built, simulating a broken release.

Start the availability check in the second tab (70 seconds):

```bash
kubectl -n cloudops-bridge exec -i deploy/bridge -- python - http://ticket-service:8080/tickets 70 < kubernetes/tools/check_availability.py
```

Main tab:

```bash
kubectl -n cloudops-bridge set image deployment/ticket-service ticket-service=cloudops-bridge-ticket-service:phase3-broken
kubectl -n cloudops-bridge annotate deployment/ticket-service kubernetes.io/change-cause="BAD: image phase3-broken" --overwrite
kubectl -n cloudops-bridge rollout status deployment/ticket-service --timeout=20s
kubectl -n cloudops-bridge get pods -l app.kubernetes.io/name=ticket-service
```

**What to observe:** `rollout status` times out (`error: timed out waiting for the condition`). There's one new pod in `ErrImageNeverPull`, and **both old pods are still `1/1 Running`** with their original names and ages. Because of `maxUnavailable: 0`, Kubernetes won't remove an old pod until a new one is Ready, and that never happens.

Investigate it the way an on-call engineer would:

```bash
kubectl -n cloudops-bridge get deployment ticket-service
kubectl -n cloudops-bridge describe pod $(kubectl -n cloudops-bridge get pods -l app.kubernetes.io/name=ticket-service --field-selector=status.phase=Pending -o jsonpath='{.items[0].metadata.name}')
kubectl -n cloudops-bridge rollout history deployment/ticket-service
```

The `describe` Events show `Container image "...:phase3-broken" is not present with pull policy of Never`. The history shows the bad revision at the top.

Roll back and confirm:

```bash
kubectl -n cloudops-bridge rollout undo deployment/ticket-service
kubectl -n cloudops-bridge rollout status deployment/ticket-service
kubectl -n cloudops-bridge get deploy ticket-service -o jsonpath='{.spec.template.spec.containers[0].image}{"\n"}'
kubectl -n cloudops-bridge rollout history deployment/ticket-service
kubectl -n cloudops-bridge exec -i deploy/bridge -- python - < kubernetes/tools/smoke_test.py
```

kubectl 1.37 also prints `Warning: ... Rolling back will not update the kubectl.kubernetes.io/last-applied-configuration annotation`. That's expected: `undo` changes the live object outside `kubectl apply`'s bookkeeping, which is one reason to finish with [Restore the declared state](#restore-the-declared-state).

Expected: `successfully rolled out`, image back to `cloudops-bridge-ticket-service:phase3`, and the previous good revision moved to the top of the history with a new number (undo creates a new revision). The availability check should report `0 failed`.

**What this proves:** a broken release never took capacity away from users. Rolling back is a single command, and it is recorded in the history.

**Rollback trap (found while testing the runbook):** a plain `rollout undo` goes to the *immediately previous* revision. Here that was the good one. But run `undo` again after this demo and the previous revision is now the **bad** image, so `undo` would redeploy the broken release. That really happened during testing; `maxUnavailable: 0` kept users unaffected. In a real incident, check `rollout history deployment/NAME --revision=N` and use `rollout undo --to-revision=N`, as the runbook does.

**Caveat about CHANGE-CAUSE:** it's just an annotation on the Deployment, copied into each new revision. If you don't update it, the next rollout (for example a `kubectl apply`) inherits the old text, so the history can be misleading. Annotate every change.

### Restore the declared state

Demos C and D changed the live Deployment imperatively. Re-apply the manifests so every field declared in Git is enforced again. If `rollout undo` already restored them, apply reports `unchanged`. The `restartedAt` annotation from `rollout restart` isn't declared in Git, so apply leaves it alone:

```bash
kubectl apply -R -f kubernetes/
kubectl -n cloudops-bridge rollout status deployment/ticket-service
kubectl -n cloudops-bridge rollout status deployment/bridge
```

---

## 9. Troubleshooting commands: when each one helps

| Command | Use it when |
|---|---|
| `kubectl -n cloudops-bridge get pods` | First look: is anything not `Ready`, not `Running`, or restarting? Rising `RESTARTS` means liveness failures or crashes |
| `kubectl -n cloudops-bridge describe pod POD` | A pod is stuck or restarting. The Events and `Last State` at the bottom tell you *why*: probe failures, `OOMKilled`, `ErrImageNeverPull`, scheduling problems |
| `kubectl -n cloudops-bridge logs POD` | The app is up but misbehaving. Use `deploy/NAME` to pick any pod of a Deployment |
| `kubectl -n cloudops-bridge logs POD --previous` | The container restarted (`RESTARTS` > 0). The current log starts fresh, and the evidence (exception, last request) is in the *previous* container's log. Without a restart it returns `not found` |
| `kubectl -n cloudops-bridge get events --sort-by=.lastTimestamp` | Timeline across all objects: what happened in what order. Events expire after about 1 hour |
| `kubectl -n cloudops-bridge rollout status deployment/NAME` | After any deploy: did it finish, or is it stuck? |
| `kubectl -n cloudops-bridge rollout history deployment/NAME` | "Did something change?", the most common cause of incidents |
| `kubectl -n cloudops-bridge rollout undo deployment/NAME --to-revision=N` | A recent rollout is the likely cause. Mitigate first, investigate after. Check `rollout history --revision=N` first, because a plain `undo` can land on a bad revision |
| `kubectl -n cloudops-bridge exec -it deploy/NAME -- sh` | Check what the container actually sees: mounted config, environment variables, DNS, calls to other Services |

## 10. Updating configuration

`service-catalog/*.yaml`, `runbooks/*.md` and `dashboards/*.json` are the source of truth. Their ConfigMaps are generated, and `tests/test_kubernetes_config.py` fails if they drift. After editing a catalog or runbook:

```bash
./kubernetes/generate-configmaps.sh
kubectl apply -R -f kubernetes/
kubectl -n cloudops-bridge rollout restart deployment/bridge
```

Kubernetes updates mounted ConfigMap files in about a minute, but the bridge reads the catalog only at startup, so the `rollout restart` is what makes it take effect.

## 11. Cleanup and rebuild from scratch

Remove only the application:

```bash
kubectl delete namespace cloudops-bridge
```

Remove the whole cluster (this also removes its kubectl context):

```bash
kind delete cluster --name cloudops-bridge
kind get clusters
```

Everything is declared in this repository. To rebuild, run sections 2–4 again. No manual state is required.

## Design decisions

| Setting | Value | Why |
|---|---|---|
| Replicas | 2 | Survives one pod failing; makes rollouts and reconciliation visible |
| Strategy | `maxUnavailable: 0`, `maxSurge: 1` | Never below 2 Ready pods; one extra pod at a time keeps the small local cluster light |
| Readiness probe | `/ready`, delay 2 s, every 5 s, timeout 2 s, 2 failures | Pulls a bad pod out of traffic within ~10 s; tolerates one slow response |
| Liveness probe | `/health`, delay 5 s, every 10 s, timeout 2 s, 3 failures | Restarts only after ~30 s of consecutive failure, so a brief stall doesn't cause a restart loop. Always slower than readiness |
| Requests | 50m CPU, 64Mi memory | Each process uses about 34 MiB idle (measured). Requests decide scheduling, and HPA in a later phase computes utilization against them |
| Limits | 500m CPU, 128Mi memory | Caps a runaway pod. Going over the memory limit means `OOMKilled`; going over the CPU limit means throttling, not killing |
| `preStop` sleep | 5 s | Prevents dropped requests during rollouts (see Demo C) |
| `terminationGracePeriodSeconds` | 15 | 5 s preStop + under 1 s uvicorn shutdown, with margin |
| `runAsNonRoot`, `runAsUser/Group: 10001` | | Matches the image's `app` user. Kubernetes refuses to start the pod if it would run as root |
| `allowPrivilegeEscalation: false` | | Blocks setuid and similar escalation (`NoNewPrivs: 1`) |
| `readOnlyRootFilesystem: true` | | Tested first: neither app writes to disk (`PYTHONDONTWRITEBYTECODE=1`, logs go to stdout) |
| `capabilities.drop: [ALL]` | | Ports 8080 and 8081 are above 1024, so no capability is needed |
| `seccompProfile: RuntimeDefault` | | Blocks unusual system calls; the apps need none of them |
| ConfigMaps, not Secrets | | The catalog and runbooks aren't secret |
| Bridge `/ready` | | 200 only when the catalog is loaded. Keeps "restart me" (liveness) separate from "don't route to me" (readiness) |

**Known limitation:** each ticket-service pod keeps its own in-memory inventory. With 2 replicas, purchases routed to different pods give different `available` counts. PostgreSQL (future phase) fixes this. It's a good example of why stateless pods need external state.
