#!/usr/bin/env bash
# Validate the alerting configuration with the real tools, using the same
# pinned images that run in the cluster (no local install needed):
#
#   promtool check config   prometheus.yml, including the rule files it loads
#   promtool check rules    alert rule syntax and PromQL
#   promtool test rules     unit tests in tests/prometheus/ (synthetic series)
#   amtool check-config     alertmanager.yml
#
# The files are extracted from the ConfigMaps, so what is validated is exactly
# what Kubernetes mounts. Requires Docker. Run from anywhere:
#   ./kubernetes/monitoring/validate-alerting.sh

set -euo pipefail

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
MON="$ROOT/kubernetes/monitoring"
PROMETHEUS_IMAGE="$(awk '/image: prom\/prometheus:/ {print $2}' "$MON/prometheus/deployment.yaml")"
ALERTMANAGER_IMAGE="$(awk '/image: prom\/alertmanager:/ {print $2}' "$MON/alertmanager/deployment.yaml")"

WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT
mkdir -p "$WORK/etc/prometheus" "$WORK/etc/prometheus-rules" "$WORK/etc/alertmanager" "$WORK/tests"

extract() {
  python3 - "$1" "$2" "$3" <<'PY'
import sys, yaml
manifest, key, out = sys.argv[1:]
data = yaml.safe_load(open(manifest))["data"][key]
open(out, "w").write(data)
PY
}

extract "$MON/prometheus/configmap.yaml" prometheus.yml "$WORK/etc/prometheus/prometheus.yml"
extract "$MON/prometheus/rules-configmap.yaml" ticket-service.yml "$WORK/etc/prometheus-rules/ticket-service.yml"
extract "$MON/alertmanager/configmap.yaml" alertmanager.yml "$WORK/etc/alertmanager/alertmanager.yml"
cp "$WORK/etc/prometheus-rules/ticket-service.yml" "$WORK/tests/"
cp "$ROOT"/tests/prometheus/*.test.yml "$WORK/tests/"
# promtool checks that the kubelet job's service-account token file exists.
# In the cluster Kubernetes mounts the real one; for this offline syntax check
# an empty placeholder is mounted at the same path (no credential involved).
: > "$WORK/token-placeholder"

run() {
  local image="$1" tool="$2"; shift 2
  docker run --rm -v "$WORK/etc/prometheus:/etc/prometheus:ro" \
    -v "$WORK/etc/prometheus-rules:/etc/prometheus-rules:ro" \
    -v "$WORK/etc/alertmanager:/etc/alertmanager:ro" \
    -v "$WORK/tests:/tests:ro" -w /tests \
    -v "$WORK/token-placeholder:/var/run/secrets/kubernetes.io/serviceaccount/token:ro" \
    --entrypoint "/bin/$tool" "$image" "$@"
}

echo "== promtool check config ($PROMETHEUS_IMAGE)"
run "$PROMETHEUS_IMAGE" promtool check config /etc/prometheus/prometheus.yml
echo "== promtool check rules"
run "$PROMETHEUS_IMAGE" promtool check rules /etc/prometheus-rules/ticket-service.yml
echo "== promtool test rules"
run "$PROMETHEUS_IMAGE" promtool test rules /tests/ticket-service-alerts.test.yml
echo "== amtool check-config ($ALERTMANAGER_IMAGE)"
run "$ALERTMANAGER_IMAGE" amtool check-config /etc/alertmanager/alertmanager.yml
