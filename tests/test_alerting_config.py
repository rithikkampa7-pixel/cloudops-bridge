"""Static checks on the alerting configuration.

Rule *logic* is unit-tested with promtool (tests/prometheus/, run by
kubernetes/monitoring/validate-alerting.sh). These tests protect the wiring
and contracts that promtool cannot see: that Prometheus actually loads the
mounted rules and reaches the real Alertmanager Service, that alert labels
match the service catalog (the Bridge maps alerts with them), and that
Alertmanager sends ticket-api alerts only to the in-cluster Bridge webhook.
"""

import fnmatch
import re
from pathlib import Path
from urllib.parse import urlparse

import yaml

ROOT = Path(__file__).resolve().parent.parent
MON = ROOT / "kubernetes" / "monitoring"
K8S = ROOT / "kubernetes"
CATALOG = yaml.safe_load((ROOT / "service-catalog" / "ticket-api.yaml").read_text())


def manifest(path):
    return yaml.safe_load((MON / path).read_text())


def embedded(path, key):
    return yaml.safe_load(manifest(path)["data"][key])


PROMETHEUS = embedded("prometheus/configmap.yaml", "prometheus.yml")
RULES = {
    rule["alert"]: rule
    for key in manifest("prometheus/rules-configmap.yaml")["data"]
    for group in embedded("prometheus/rules-configmap.yaml", key)["groups"]
    for rule in group["rules"]
}
ALERTMANAGER = embedded("alertmanager/configmap.yaml", "alertmanager.yml")


def test_prometheus_loads_the_mounted_rules_configmap():
    deployment = manifest("prometheus/deployment.yaml")["spec"]["template"]["spec"]
    volumes = {v["name"]: v for v in deployment["volumes"]}
    mounts = deployment["containers"][0]["volumeMounts"]
    rules_cm = manifest("prometheus/rules-configmap.yaml")
    mount = next(m for m in mounts
                 if volumes[m["name"]].get("configMap", {}).get("name") == rules_cm["metadata"]["name"])
    # Every rule file in the ConfigMap must match a rule_files glob at its mount path.
    for key in rules_cm["data"]:
        path = f"{mount['mountPath']}/{key}"
        assert any(fnmatch.fnmatch(path, g) for g in PROMETHEUS["rule_files"]), path
    # Explicit: the 1m default would make alert timing coarse.
    assert PROMETHEUS["global"]["evaluation_interval"] == "15s"


def test_prometheus_sends_alerts_to_the_alertmanager_service():
    service = manifest("alertmanager/service.yaml")
    meta, port = service["metadata"], service["spec"]["ports"][0]["port"]
    targets = PROMETHEUS["alerting"]["alertmanagers"][0]["static_configs"][0]["targets"]
    assert targets == [f"{meta['name']}.{meta['namespace']}.svc:{port}"]


def test_exactly_the_two_supported_alerts():
    # HighLatency is deliberately absent: the app exports no latency metric.
    assert set(RULES) == {"HighErrorRate", "TicketServiceTargetDown"}


def test_high_error_rate_counts_only_server_errors():
    expr = RULES["HighErrorRate"]["expr"]
    status_matchers = re.findall(r'status=~"([^"]+)"', expr)
    assert status_matchers == ["5.."], status_matchers  # numerator only; never 4xx
    assert expr.count('job="ticket-service"') == 3
    assert expr.count('path!~"/health|/ready|/metrics"') == 3  # probes/scrapes excluded
    assert re.search(r"\band\b", expr), "needs a minimum-traffic guard"


def test_target_down_is_scoped_to_ticket_service():
    assert RULES["TicketServiceTargetDown"]["expr"].strip() == 'up{job="ticket-service"} == 0'


def test_alert_labels_match_the_service_catalog():
    # The Bridge maps alerts to the catalog by these labels, so every rule
    # Prometheus can fire must have a catalog entry with the same severity.
    for name, rule in RULES.items():
        labels, annotations = rule["labels"], rule["annotations"]
        assert labels["service"] == CATALOG["service"], name
        assert labels["environment"] in CATALOG["environments"], name
        assert annotations.get("summary") and annotations.get("description"), name
        assert name in CATALOG["alerts"], f"{name} fires but has no catalog entry"
        catalog_severity = CATALOG["alerts"][name]["severity"]
        assert labels["severity"] == catalog_severity[labels["environment"]], name


def test_alertmanager_sends_ticket_api_alerts_only_to_the_bridge():
    route = ALERTMANAGER["route"]
    assert {"alertname", "service", "environment"} <= set(route["group_by"])
    receivers = {r["name"]: r for r in ALERTMANAGER["receivers"]}

    # Exactly one child route, with an explicit (not regex) service matcher.
    assert route["routes"] == [
        {"matchers": [f'service="{CATALOG["service"]}"'], "receiver": "cloudops-bridge"}
    ]
    assert set(receivers[route["receiver"]]) == {"name"}  # default: no integrations

    bridge = receivers["cloudops-bridge"]
    assert set(bridge) == {"name", "webhook_configs"}  # no email/slack/pagerduty/...
    assert len(bridge["webhook_configs"]) == 1
    webhook = bridge["webhook_configs"][0]
    assert webhook["send_resolved"] is True

    # The URL is the Bridge's in-cluster Service DNS name, port and webhook path.
    service = yaml.safe_load((K8S / "bridge" / "service.yaml").read_text())
    url = urlparse(webhook["url"])
    from bridge.main import WEBHOOK_PATH
    assert url.scheme == "http"
    assert url.hostname == f"{service['metadata']['name']}.{service['metadata']['namespace']}.svc"
    assert url.port == service["spec"]["ports"][0]["port"]
    assert url.path == WEBHOOK_PATH

    # Nothing in the parsed configuration (comments excluded) is an external
    # integration or points outside the cluster.
    text = yaml.safe_dump(ALERTMANAGER).lower()
    for forbidden in ("slack", "pagerduty", "email", "opsgenie", "msteams", "https://"):
        assert forbidden not in text, forbidden


def test_monitoring_images_are_pinned():
    for path in MON.rglob("deployment.yaml"):
        for container in yaml.safe_load(path.read_text())["spec"]["template"]["spec"]["containers"]:
            image = container["image"]
            assert re.search(r":v?\d+\.\d+\.\d+$", image), f"{path}: {image} is not pinned"


def test_alertmanager_security_context():
    spec = manifest("alertmanager/deployment.yaml")["spec"]["template"]["spec"]
    pod, container = spec["securityContext"], spec["containers"][0]["securityContext"]
    assert pod["runAsNonRoot"] is True and isinstance(pod["runAsUser"], int) and pod["runAsUser"] > 0
    assert container["readOnlyRootFilesystem"] is True
    assert container["allowPrivilegeEscalation"] is False
    assert container["capabilities"]["drop"] == ["ALL"]
