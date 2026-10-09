# GitOps with Argo CD (Phase 8, in progress)

> **Status:** in progress. Installing Argo CD and adopting ticket-service have been run on a local kind cluster. The other demonstrations haven't been run yet, so no other live behavior is claimed here. Results will be added once they have been measured.

Argo CD makes Git the desired state for **ticket-service**. It compares `kubernetes/ticket-service/` on the `phase8-gitops` branch with what is running in the cluster, reports any difference as **OutOfSync**, and applies Git's version when someone syncs. Its health and sync status show whether the last deployment succeeded.

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

Argo CD reads the `phase8-gitops` branch **from GitHub**, so the commits it should deploy must be pushed first.

```bash
kubectl apply -f gitops/appproject.yaml -f gitops/ticket-service-application.yaml
kubectl -n argocd get applications
```

## The HPA and GitOps

The HPA changes `spec.replicas` of the Deployment all the time. If Git also declared a replica count, every sync would reset the HPA's choice, and Argo CD would report OutOfSync whenever the HPA scaled. Three safeguards prevent this:

1. **Git never declares `spec.replicas`.** The Deployment has no `replicas` field, so the HPA is its only owner. Argo CD diffs only what Git declares, so the HPA's changes aren't drift.
2. **`ignoreDifferences` on `/spec/replicas`** of the `ticket-service` Deployment, in case anything else ever puts the field into the comparison.
3. **`RespectIgnoreDifferences=true`**, so the ignored field is also left alone *during a sync*, not only when Argo CD compares states.

Argo CD manages the HPA's own spec (2–6 replicas, 70% CPU) from Git like any other resource.

## Sync policy

- **Manual sync for now.** The Application has no `automated` block, so a Git change makes it **OutOfSync** and nothing changes in the cluster until someone syncs. This keeps the difference between Git and the cluster visible for the deployment and failure demonstrations.
- **Planned next:** automatic sync with **self-heal** after the manual tests pass. Self-heal reverts manual changes to Git-managed fields.
- **No automatic pruning,** now or later. Removing a file from Git won't delete the live resource without someone choosing to. With manual sync, leave **Prune** unticked in the sync dialog.
- **The namespace isn't Argo CD's:** `CreateNamespace=false`. The Application has no resources finalizer, so deleting the Application leaves ticket-service running.

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
