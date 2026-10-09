"""Static checks on the Phase 4 monitoring configuration.

They catch mistakes that would otherwise only show up as an empty or
misleading dashboard on a live cluster: discovery that no longer matches the
Deployment's labels, counters graphed as if they were gauges, per-pod
inventory summed into a fake shared total, or a credential committed to Git.
"""

import json
import re
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
K8S = ROOT / "kubernetes"
DASHBOARD = json.loads((ROOT / "dashboards" / "ticket-service-overview.json").read_text())
EXPRS = [t["expr"] for p in DASHBOARD["panels"] for t in p.get("targets", [])]


def load(path):
    return list(yaml.safe_load_all((K8S / path).read_text()))


def embedded_yaml(configmap_path, key):
    return yaml.safe_load(load(configmap_path)[0]["data"][key])


def ticket_job():
    config = embedded_yaml("monitoring/prometheus/configmap.yaml", "prometheus.yml")
    return next(j for j in config["scrape_configs"] if j["job_name"] == "ticket-service")


def test_dashboard_identity_and_datasource():
    assert DASHBOARD["title"] == "CloudOps Bridge - Ticket Service Overview"
    datasource = embedded_yaml("monitoring/grafana/datasource-configmap.yaml", "prometheus.yaml")
    uid = datasource["datasources"][0]["uid"]
    for panel in (p for p in DASHBOARD["panels"] if p["type"] != "row"):
        assert panel["datasource"]["uid"] == uid, panel["title"]
        for target in panel["targets"]:
            # App metrics are scoped by job; Kubernetes metrics by namespace.
            assert ('job="ticket-service"' in target["expr"]
                    or 'namespace="cloudops-bridge"' in target["expr"]), (panel["title"], target["expr"])


def test_dashboard_counters_use_rate_or_increase():
    # A raw counter only ever goes up (and drops on pod restart); graphing it
    # directly is misleading. Every *_total must be wrapped in rate()/increase().
    for expr in EXPRS:
        for match in re.finditer(r"\w+_total\{", expr):
            before = expr[: match.start()]
            assert before.endswith(("rate(", "increase(")), expr


def test_dashboard_does_not_aggregate_per_pod_inventory():
    # Inventory lives in each pod's memory; summing or averaging it would
    # imply a shared inventory that does not exist.
    for expr in (e for e in EXPRS if "tickets_available" in e):
        assert not re.search(r"\b(sum|avg|max|min)\s*(by|without|\()", expr), expr


def test_prometheus_discovers_ticket_service_pods():
    job = ticket_job()
    sd = job["kubernetes_sd_configs"][0]
    deployment = load("ticket-service/deployment.yaml")[0]
    pod_labels = deployment["spec"]["template"]["metadata"]["labels"]
    container = deployment["spec"]["template"]["spec"]["containers"][0]

    assert job["metrics_path"] == "/metrics"
    assert sd["role"] == "pod"
    assert sd["namespaces"]["names"] == [deployment["metadata"]["namespace"]]
    key, value = sd["selectors"][0]["label"].split("=")
    assert pod_labels[key] == value

    keep_port = next(r for r in job["relabel_configs"]
                     if r["source_labels"] == ["__meta_kubernetes_pod_container_port_name"])
    assert keep_port["regex"] in [p["name"] for p in container["ports"]]
    assert any(r.get("target_label") == "pod" for r in job["relabel_configs"])


def test_grafana_datasource_points_at_prometheus_service():
    datasource = embedded_yaml("monitoring/grafana/datasource-configmap.yaml", "prometheus.yaml")
    service = load("monitoring/prometheus/service.yaml")[0]
    meta, port = service["metadata"], service["spec"]["ports"][0]["port"]
    expected = f"http://{meta['name']}.{meta['namespace']}.svc:{port}"
    assert datasource["datasources"][0]["url"] == expected


def test_prometheus_rbac_is_minimal_and_read_only():
    docs = {d["kind"]: d for d in load("monitoring/prometheus/rbac.yaml")}
    # Pod discovery: namespaced and read-only.
    assert docs["Role"]["metadata"]["namespace"] == "cloudops-bridge"
    assert docs["Role"]["rules"] == [
        {"apiGroups": [""], "resources": ["pods"], "verbs": ["get", "list", "watch"]}
    ]
    deployment = load("monitoring/prometheus/deployment.yaml")[0]
    sa = deployment["spec"]["template"]["spec"]["serviceAccountName"]
    for binding in (docs["RoleBinding"], docs["ClusterRoleBinding"]):
        subject = binding["subjects"][0]
        assert (subject["name"], subject["namespace"]) == (sa, deployment["metadata"]["namespace"])
    # The only cluster-scoped access (Phase 7, kubelet resource metrics):
    # list/watch nodes and read node metrics. Never nodes/proxy, which would
    # also reach the kubelet's exec/logs API.
    assert docs["ClusterRole"]["rules"] == [
        {"apiGroups": [""], "resources": ["nodes"], "verbs": ["list", "watch"]},
        {"apiGroups": [""], "resources": ["nodes/metrics"], "verbs": ["get"]},
    ]
    assert docs["ClusterRoleBinding"]["roleRef"]["name"] == docs["ClusterRole"]["metadata"]["name"]


def test_no_credentials_committed():
    for path in K8S.rglob("*.yaml"):
        for doc in yaml.safe_load_all(path.read_text()):
            assert doc.get("kind") != "Secret", f"{path} commits a Secret"
    grafana = load("monitoring/grafana/deployment.yaml")[0]
    env = {e["name"]: e for e in grafana["spec"]["template"]["spec"]["containers"][0]["env"]}
    password = env["GF_SECURITY_ADMIN_PASSWORD"]
    assert "value" not in password and "secretKeyRef" in password["valueFrom"]


def test_grafana_read_only_root_keeps_bundled_plugins():
    # Found live: with a read-only root filesystem, Grafana 13's background
    # plugin installer unregisters the bundled Prometheus plugin, fails to
    # replace it, and every panel errors with "Plugin not registered".
    grafana = load("monitoring/grafana/deployment.yaml")[0]
    container = grafana["spec"]["template"]["spec"]["containers"][0]
    env = {e["name"]: e.get("value") for e in container["env"]}
    if container["securityContext"].get("readOnlyRootFilesystem"):
        assert env.get("GF_PLUGINS_PREINSTALL_DISABLED") == "true"


def test_dashboard_queries_only_metrics_prometheus_collects():
    # Every metric a panel uses must come from a configured scrape job, so no
    # panel can silently show "No data" because its source was never added.
    config = embedded_yaml("monitoring/prometheus/configmap.yaml", "prometheus.yml")
    jobs = {j["job_name"]: j for j in config["scrape_configs"]}
    app = {"up", "http_requests_total", "tickets_available", "tickets_sold_total"}
    keep = jobs["kubelet-resource"]["metric_relabel_configs"][0]["regex"].split(";")[0]
    kubelet = set(keep.strip("()").split("|"))
    assert "kube-state-metrics" in jobs
    used = {m for e in EXPRS for m in re.findall(r"([a-zA-Z_:][a-zA-Z0-9_:]*)\{", e)}
    for metric in used:
        assert metric in app | kubelet or metric.startswith("kube_"), metric
