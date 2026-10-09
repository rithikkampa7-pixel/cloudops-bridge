"""Phase 7: HPA, metrics-server, kube-state-metrics and the load generator."""

import hashlib
import importlib.util
import random
import re
import socket
import urllib.error
from collections import Counter
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parent.parent
K8S = ROOT / "kubernetes"


def load_all(path):
    return [d for d in yaml.safe_load_all(path.read_text()) if d]


HPA = load_all(K8S / "ticket-service" / "hpa.yaml")[0]
DEPLOYMENT = load_all(K8S / "ticket-service" / "deployment.yaml")[0]

spec = importlib.util.spec_from_file_location("ticket_sale", ROOT / "loadtest" / "ticket_sale.py")
ticket_sale = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ticket_sale)


# ---- HPA and the Deployment it scales ----

def test_hpa_scales_the_ticket_service_deployment_on_cpu():
    target = HPA["spec"]["scaleTargetRef"]
    assert (target["kind"], target["name"]) == ("Deployment", DEPLOYMENT["metadata"]["name"])
    assert HPA["metadata"]["namespace"] == DEPLOYMENT["metadata"]["namespace"]
    assert HPA["spec"]["minReplicas"] == 2          # the pre-HPA safe baseline
    assert 2 < HPA["spec"]["maxReplicas"] <= 6      # bounded local demo
    (metric,) = HPA["spec"]["metrics"]
    assert metric["type"] == "Resource" and metric["resource"]["name"] == "cpu"
    assert metric["resource"]["target"] == {"type": "Utilization", "averageUtilization": 70}
    assert HPA["spec"]["behavior"]["scaleDown"]["stabilizationWindowSeconds"] == 300


def test_deployment_has_cpu_request_and_leaves_replicas_to_the_hpa():
    container = DEPLOYMENT["spec"]["template"]["spec"]["containers"][0]
    # Utilization is usage / request: without a CPU request the HPA cannot compute it.
    assert container["resources"]["requests"]["cpu"] == "50m"
    # A replicas field would be reset to its value by every `kubectl apply`.
    assert "replicas" not in DEPLOYMENT["spec"]


# ---- metrics-server (vendored) ----

def test_metrics_server_is_pinned_and_only_adds_kind_tls_flag():
    path = K8S / "metrics-server" / "components.yaml"
    text = path.read_text()
    upstream_sha = re.search(r"Upstream sha256: ([0-9a-f]{64})", text).group(1)
    # Rebuild the upstream file: drop our marked header and our one marked line.
    upstream = "".join(line for line in text.splitlines(keepends=True) if "CLOUDOPS-BRIDGE" not in line)
    assert hashlib.sha256(upstream.encode()).hexdigest() == upstream_sha
    added = [line.split("#")[0].strip() for line in text.splitlines()
             if "CLOUDOPS-BRIDGE" in line and not line.startswith("#")]
    assert added == ["- --kubelet-insecure-tls"]
    images = re.findall(r"image: (\S+)", text)
    assert images == ["registry.k8s.io/metrics-server/metrics-server:v0.9.0"]


# ---- kube-state-metrics ----

def test_kube_state_metrics_is_pinned_namespaced_and_read_only():
    base = K8S / "monitoring" / "kube-state-metrics"
    deployment = load_all(base / "deployment.yaml")[0]
    container = deployment["spec"]["template"]["spec"]["containers"][0]
    assert container["image"] == "registry.k8s.io/kube-state-metrics/kube-state-metrics:v2.20.0"
    assert "--namespaces=cloudops-bridge" in container["args"]
    assert "--resources=deployments,horizontalpodautoscalers,pods" in container["args"]
    docs = {d["kind"]: d for d in load_all(base / "rbac.yaml")}
    assert "ClusterRole" not in docs and "ClusterRoleBinding" not in docs
    assert docs["Role"]["metadata"]["namespace"] == "cloudops-bridge"
    for rule in docs["Role"]["rules"]:
        assert rule["verbs"] == ["list", "watch"], rule


def test_kubelet_scrape_keeps_only_two_metrics_for_one_namespace():
    cm = load_all(K8S / "monitoring" / "prometheus" / "configmap.yaml")[0]
    config = yaml.safe_load(cm["data"]["prometheus.yml"])
    job = next(j for j in config["scrape_configs"] if j["job_name"] == "kubelet-resource")
    assert job["metrics_path"] == "/metrics/resource"
    assert job["metric_relabel_configs"] == [{
        "source_labels": ["__name__", "namespace"],
        "regex": "(container_cpu_usage_seconds_total|container_memory_working_set_bytes);cloudops-bridge",
        "action": "keep",
    }]


# ---- Load generator (pure logic) ----

def test_profile_parsing():
    assert ticket_sale.parse_profile("normal:20:180,spike:200:300") == [
        ("normal", 20.0, 180.0), ("spike", 200.0, 300.0)]
    for bad in ("", "x:0:10", "x:10:0", "x:10"):
        with pytest.raises(ValueError):
            ticket_sale.parse_profile(bad)


def test_request_mix_is_valid_and_respects_buy_ratio():
    rng = random.Random(7)
    picks = [ticket_sale.choose_request(rng, 0.05) for _ in range(20000)]
    kinds = Counter((m, p) for m, p, _ in picks)
    assert set(kinds) == {("GET", "/tickets"), ("POST", "/tickets/purchase")}  # no invalid requests
    assert all(body == {"quantity": 1} for m, _, body in picks if m == "POST")
    assert 0.04 < kinds[("POST", "/tickets/purchase")] / len(picks) < 0.06


@pytest.mark.parametrize("exc, category", [
    (urllib.error.HTTPError("u", 503, "x", {}, None), "http_503"),
    (urllib.error.URLError(socket.timeout("t")), "timeout"),
    (socket.timeout("t"), "timeout"),
    (urllib.error.URLError(ConnectionRefusedError()), "connection_error"),
    (ConnectionResetError(), "connection_error"),
    (ValueError("x"), "other_error"),
])
def test_failures_are_classified(exc, category):
    assert ticket_sale.classify(exc) == category


def test_summary_reports_achieved_rate_failures_and_saturation_separately():
    counts = Counter(ok=95, http_503=2, timeout=2, connection_error=1, skipped=4)
    s = ticket_sale.summarize(counts, [0.01] * 100, seconds=10)
    assert s["sent"] == 100 and s["achieved_rps"] == 10.0
    assert s["ok"] == 95 and s["failed"] == 5
    assert s["failures"] == {"connection_error": 1, "http_503": 2, "timeout": 2}
    assert s["skipped_generator_saturated"] == 4  # not counted as sent or failed
    assert s["p95_ms"] == 10.0


def test_load_job_is_separate_from_the_application():
    job = load_all(ROOT / "loadtest" / "ticket-sale-job.yaml")[0]
    labels = job["spec"]["template"]["metadata"]["labels"]
    # Must not match the ticket-service selector (Service, HPA, Prometheus discovery).
    assert labels["app.kubernetes.io/name"] != "ticket-service"
    assert not (ROOT / "kubernetes" / "loadtest").exists()  # never started by apply -R
    container = job["spec"]["template"]["spec"]["containers"][0]
    assert container["resources"]["limits"]["cpu"]
    assert container["securityContext"]["readOnlyRootFilesystem"] is True
