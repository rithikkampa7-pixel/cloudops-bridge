# GitOps with Argo CD

> Installation, adoption, HPA scaling under Argo CD, a Git-driven deployment, a failed release recovered with `git revert`, self-heal, and an incident regression have all been run on a local kind cluster. See [Verified results](#verified-results).

Argo CD makes Git the desired state for **ticket-service**. It compares `kubernetes/ticket-service/` on the branch named in the Application's `targetRevision` (`phase8-gitops`) with what is running in the cluster, reports any difference as **OutOfSync**, and applies Git's version automatically (see [Sync policy](#sync-policy)). Its health and sync status show whether the last deployment succeeded.

This is a **local demonstration** on the same single-node kind cluster as the rest of the project. There is no image registry: images are still built locally and loaded with `kind load`. Argo CD deploys manifests, not images.

## Scope

| Managed by | What |
|---|---|
| **Argo CD** (Application `ticket-service`) | `kubernetes/ticket-service/`: Deployment, Service, HorizontalPodAutoscaler |
| `kubectl apply` (README Quick start) | Namespaces, Bridge, generated ConfigMaps, monitoring, metrics-server |

Only ticket-service is managed by Argo CD, to keep the change small and easy to review. This folder sits outside `kubernetes/` so the Quick start's `kubectl apply -R -f kubernetes/` never applies Argo CD objects.

## Ownership before and after adoption

**Before adoption,** the README Quick start creates every resource, including ticket-service, with `kubectl apply -R -f kubernetes/`. That is how a new cluster is built.

**After adoption** (once the Application has been synced), ticket-service is GitOps-managed. On an adopted cluster:

- **Don't rerun `kubectl apply -R -f kubernetes/`.**
- **Don't `kubectl apply` any file under `kubernetes/ticket-service/` directly.**
- Change ticket-service's desired state only like this: **edit in Git → commit → push → Argo CD compares → Argo CD syncs**.

**Why:** the HPA owns `spec.replicas`, and the Application tells Argo CD to ignore that field (`ignoreDifferences` plus `RespectIgnoreDifferences=true`). To leave the field alone during a sync, Argo CD applies the Git manifest with the **live** replica count filled in. That value is then recorded in kubectl's `kubectl.kubernetes.io/last-applied-configuration` annotation. A later plain `kubectl apply` of the Git file, which has no `replicas`, sees the field as removed and deletes it. The Deployment then falls back to 1 replica until the HPA raises it again.

During adoption, the sync wrote `"replicas":2` into that annotation. A server-side dry run of `kubectl apply -f kubernetes/ticket-service/deployment.yaml` then returned `spec.replicas=1`; the live Deployment wasn't changed. This is the expected result of mixing two ways of applying the same object (kubectl's three-way merge and Argo CD's handling of ignored fields), not an Argo CD bug. The fix is to give ticket-service a single owner.

The other resources (Bridge, ConfigMaps, monitoring, metrics-server) aren't managed by Argo CD and are still applied with `kubectl`. To apply a change to one of them on an adopted cluster, apply that file or folder only (for example `kubectl apply -f kubernetes/monitoring/grafana/`), never all of `kubernetes/`.

## Files

| File | Purpose |
|---|---|
| `install-argocd.sh` | Installs pinned Argo CD into the `argocd` namespace after verifying the manifest's SHA-256 |
| `appproject.yaml` | AppProject `cloudops-bridge`: allowed repository, destination and resource kinds |
| `ticket-service-application.yaml` | Application `ticket-service`: source, destination, sync policy, HPA safeguards |

`tests/test_gitops_config.py` checks every setting described below.

## Argo CD version and verification

- **Version:** Argo CD **v3.5.4**, the non-HA `install.yaml` from the official repository at that tag. It includes the fixes for the critical advisories listed in the v3.5.4 release notes.
- **Integrity:** the script downloads the manifest over HTTPS and compares its SHA-256 with the value pinned in the script (`1feb02cc…e010`). On a mismatch it prints both values and exits **before applying anything**.
- **Safety:** it refuses to run unless the current kubectl context is `kind-cloudops-bridge`. Rerunning it is safe: the namespace is created only if missing, and applying the same manifest again changes nothing.
- **Upgrading:** change `ARGOCD_VERSION` and `ARGOCD_INSTALL_SHA256` together, after reading the release notes and upgrade guide.

## Install

After the [Quick start](../README.md#quick-start-kind):

```bash
./gitops/install-argocd.sh
```

## Credentials

Argo CD generates its own `admin` password into the Secret `argocd-initial-admin-secret` inside the cluster. It is never stored in Git, and the install script never reads or prints it. To log in, copy it to your clipboard yourself, start the port-forward in its own tab, and open https://localhost:18443 (Argo CD uses a self-signed certificate, so the browser will warn):

```bash
kubectl -n argocd get secret argocd-initial-admin-secret -o jsonpath='{.data.password}' | base64 -d | pbcopy
kubectl -n argocd port-forward svc/argocd-server 18443:443
```

The upstream documentation recommends changing the password after the first login (User Info → Update Password) and then deleting `argocd-initial-admin-secret`.

## Register the application

Argo CD reads the `targetRevision` branch (`phase8-gitops`) **from GitHub**, so the commits it should deploy must be pushed first.

```bash
kubectl apply -f gitops/appproject.yaml -f gitops/ticket-service-application.yaml
kubectl -n argocd get applications
```

The AppProject and Application live in `gitops/`, outside the path Argo CD manages, so Argo CD doesn't update them from Git. After changing either file in Git, apply that file again with the command above.

## The HPA and GitOps

The HPA changes `spec.replicas` of the Deployment all the time. If Git also declared a replica count, every sync would reset the HPA's choice, and Argo CD would report OutOfSync whenever the HPA scaled. Three safeguards prevent this:

1. **Git never declares `spec.replicas`.** The Deployment has no `replicas` field, so the HPA is its only owner. Argo CD diffs only what Git declares, so the HPA's changes aren't drift.
2. **`ignoreDifferences` on `/spec/replicas`** of the `ticket-service` Deployment, in case anything else ever puts the field into the comparison.
3. **`RespectIgnoreDifferences=true`**, so the ignored field is also left alone *during a sync*, not only when Argo CD compares states.

Argo CD manages the HPA's own spec (2–6 replicas, 70% CPU) from Git like any other resource.

## Sync policy

The Application uses **automated sync with self-heal** (`automated.enabled: true`, `selfHeal: true`) and **no automatic pruning** (`prune: false`).

| | Manual sync (used for the deployment and failure demonstrations) | Automated sync with self-heal (current) |
|---|---|---|
| A Git change | Git change → CI → someone reviews the OutOfSync diff → manual sync | Git change → Argo CD detects the new commit → deploys it automatically |
| A manual `kubectl` change to a Git-managed field | Reported as OutOfSync; stays until someone syncs | Reverted to the Git value automatically |

- **Argo CD doesn't wait for CI.** It deploys a new commit when it sees it (by polling or a manual refresh), whether or not GitHub Actions has finished or passed. So this local demonstration has **no CI-enforced approval gate**. A real setup would need one, for example branch protection that only lets reviewed, CI-passing changes reach the branch Argo CD watches, or a separate promotion step between environments. The failed-release demonstration also showed that CI can pass a release whose image doesn't exist in the cluster.
- **No automatic pruning.** Removing a file from Git won't delete the live resource unless someone chooses to.
- **The namespace isn't Argo CD's:** `CreateNamespace=false`. The Application has no resources finalizer, so deleting the Application leaves ticket-service running.
- **The HPA is unaffected:** self-heal leaves `spec.replicas` alone because of the safeguards in [The HPA and GitOps](#the-hpa-and-gitops).

### Turning automated sync off

To return to manual sync, set `automated.enabled: false` in `ticket-service-application.yaml`, commit and push, then apply the file. Running ticket-service isn't changed:

```bash
kubectl apply -f gitops/ticket-service-application.yaml
kubectl -n argocd get application ticket-service -o jsonpath='{.spec.syncPolicy.automated}{"\n"}'
```

In an emergency, the same change can be made directly on the live Application. Update the file in Git afterwards, or the next apply of the file turns automated sync back on:

```bash
kubectl -n argocd patch application ticket-service --type merge -p '{"spec":{"syncPolicy":{"automated":{"enabled":false}}}}'
```

## Verified results

Each test is a separate run on the local single-node kind cluster (Kubernetes 1.37.0, Argo CD v3.5.4). Availability was measured by an in-cluster checker calling `GET /tickets` through the Service 5 times per second. The deployment and failed-release tests used manual sync; self-heal and the incident test ran with automated sync.

| Test | Result |
|---|---|
| **Adoption** of the running ticket-service | First comparison: OutOfSync, with the only difference being Argo CD's `tracking-id` annotation on the Service, Deployment and HPA. After one manual sync (no prune, force or replace): Synced/Healthy, with the same pod names, UIDs, container IDs and 0 restarts |
| **HPA under Argo CD**: the on-sale load profile (20 → 200 → 20 req/s) | 2 → 3 (+24 s) → 6 (+40 s), then 6 → 3 → 2 after the stabilization window. Synced in 976 of 976 samples (every ~2 s); health Progressing for about 15 s in total while new pods started; no sync operation; `spec.replicas` changed only by the HPA. Load generator: 73,185 requests, 0 failed responses, 15 not sent (generator saturated at the start of the spike, so its Job reported failure). Availability: 10,223 ok, 0 failed |
| **Git-driven deployment** (`:phase3` → `:phase8-v2`, same image content, new tag) | CI green → OutOfSync with a one-line image diff → manual sync → rolling update finished in about 14 s, with Ready pods never below 2. Health then went **Degraded for 30 s** because the HPA had no CPU metrics for the new pods yet (`FailedGetResourceMetric`). Availability: **2,913 / 2,913** ok |
| **Failed release** (image tag that was never built) | CI passed (it can't see the cluster's images). After the sync: new pod `ErrImageNeverPull`, `ProgressDeadlineExceeded` 121 s later, Argo CD **Synced and Degraded**; the old pods stayed the only ready endpoints. `git revert` → CI green → sync: Synced/Healthy about 2 s after the sync started (the old ReplicaSet was reused). Availability over the whole failure and recovery: **8,708 / 8,708** ok |
| **Self-heal** | `kubectl patch` set `progressDeadlineSeconds` to 121 (Git: 120). Argo CD's automated sync (`initiatedBy: automated`) restored 120 about 1.2 s later, with no pod restart and no rollout. Availability: **2,911 / 2,911** ok |
| **Incident regression** (one frozen ticket-service process) | `TicketServiceTargetDown` pending +17 s, firing +32 s, enriched firing webhook +40 s, resolved webhook +100 s with the same fingerprint, 0 failed deliveries. Argo CD stayed Synced (health Progressing for 36 s) and started no sync. Availability: 2,863 / 2,867 ok: **4 requests timed out** in the ~8 s before readiness removed the frozen pod from the Service |

**Sync isn't health.** In the failed release, Argo CD was **Synced** (the cluster matched Git) and **Degraded** (the workload didn't work) at the same time. The rollout alert and the Grafana GitOps row exist because of this.

## Faster rollout failure reporting

The ticket-service Deployment sets `progressDeadlineSeconds: 120` (the Kubernetes default is 600). If a rollout makes no progress for 120 s, for example because the new image doesn't exist, Kubernetes marks it `ProgressDeadlineExceeded`, and Argo CD reports the Deployment as **Degraded**. This is set for faster feedback in the local demonstration.

It changes only how quickly a stuck rollout is *reported*. It doesn't roll anything back. The strategy is unchanged (`maxUnavailable: 0`, `maxSurge: 1`), so the old pods keep serving until a new pod is Ready.

## AppProject guardrails

| Setting | Value | Why |
|---|---|---|
| `sourceRepos` | `https://github.com/rithikkampa7-pixel/cloudops-bridge.git` | Applications in the project can deploy only from this repository |
| `destinations` | `cloudops-bridge` namespace of the in-cluster API server | Nothing can be deployed to `monitoring`, `kube-system` or `argocd` through this project |
| `clusterResourceWhitelist` | empty | No cluster-scoped resources (namespaces, ClusterRoles, CRDs) |
| `namespaceResourceWhitelist` | Deployment, Service, HorizontalPodAutoscaler | Only what ticket-service uses. Secrets, ConfigMaps, Roles and every other kind are refused |

## Limitations

- **Single local cluster,** as in the rest of the project. It isn't a production setup.
- **Argo CD's own permissions are broad.** The upstream install gives its application controller cluster-wide permissions. The AppProject limits what *this project's Applications* can deploy, not what Argo CD itself can do.
- **No GitHub webhook.** GitHub can't reach a local cluster, so Argo CD notices new commits by polling (every 3 minutes by default) or when someone clicks **Refresh**.
- **Kubernetes version.** Argo CD 3.5 is tested by upstream on Kubernetes 1.33–1.36. The kind cluster in this project runs 1.37.0, one minor version newer, so compatibility will be confirmed when it is deployed.
