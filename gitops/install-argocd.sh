#!/usr/bin/env bash
# Install a pinned Argo CD release into the "argocd" namespace of the local
# kind cluster.
#
#   1. Refuses to run unless kubectl points at the kind-cloudops-bridge context.
#   2. Downloads the official non-HA install manifest for ARGOCD_VERSION.
#   3. Verifies its SHA-256 and stops, applying nothing, if it differs.
#   4. Applies it server-side (the CRDs are too large for client-side apply)
#      and waits for every Argo CD workload to be ready.
#
# Safe to rerun: the namespace is created only if missing, and a server-side
# apply of the same manifest changes nothing. The script never reads, prints
# or copies the admin password; see gitops/README.md for how to retrieve it.
# Run from anywhere:
#   ./gitops/install-argocd.sh

set -euo pipefail

ARGOCD_VERSION="v3.5.4"
# sha256 of the file below, computed when the version was pinned. To upgrade,
# change both values together after reviewing the release notes.
ARGOCD_INSTALL_SHA256="1feb02cc7bacf3a379da58b0ca8c6f07d18288cd04d531dabd08816375e5e010"
ARGOCD_INSTALL_URL="https://raw.githubusercontent.com/argoproj/argo-cd/${ARGOCD_VERSION}/manifests/install.yaml"
EXPECTED_CONTEXT="kind-cloudops-bridge"
NAMESPACE="argocd"

context="$(kubectl config current-context)"
if [[ "$context" != "$EXPECTED_CONTEXT" ]]; then
  echo "refusing to install: kubectl context is '$context', expected '$EXPECTED_CONTEXT'" >&2
  exit 1
fi

WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT
manifest="$WORK/install.yaml"

echo "== downloading Argo CD $ARGOCD_VERSION install manifest"
curl --fail --silent --show-error --location --proto '=https' --tlsv1.2 \
  --output "$manifest" "$ARGOCD_INSTALL_URL"

if command -v sha256sum >/dev/null; then
  actual="$(sha256sum "$manifest" | awk '{print $1}')"
else
  actual="$(shasum -a 256 "$manifest" | awk '{print $1}')"
fi
if [[ "$actual" != "$ARGOCD_INSTALL_SHA256" ]]; then
  echo "SHA-256 MISMATCH: nothing was applied" >&2
  echo "  expected $ARGOCD_INSTALL_SHA256" >&2
  echo "  actual   $actual" >&2
  exit 1
fi
echo "sha256 OK ($actual)"

echo "== applying to namespace $NAMESPACE"
kubectl create namespace "$NAMESPACE" --dry-run=client -o yaml | kubectl apply -f -
# --force-conflicts is part of the upstream install command: it lets this
# apply take back fields of Argo CD's own objects that another manager changed.
kubectl apply --namespace "$NAMESPACE" --server-side --force-conflicts -f "$manifest"

echo "== waiting for Argo CD to be ready"
for deployment in argocd-redis argocd-repo-server argocd-server argocd-dex-server \
  argocd-applicationset-controller argocd-notifications-controller; do
  kubectl -n "$NAMESPACE" rollout status "deployment/$deployment" --timeout=300s
done
kubectl -n "$NAMESPACE" rollout status statefulset/argocd-application-controller --timeout=300s

echo "Argo CD $ARGOCD_VERSION is ready. Next: gitops/README.md, 'Register the application'."
