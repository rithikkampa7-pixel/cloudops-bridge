"""POST /webhooks/alertmanager with Alertmanager's v4 payload format.

These are isolated tests with representative payloads (tests/fixtures/). They
check parsing, mapping and the status policy. They are not the end-to-end
demonstration, which uses a real alert sent by the real Alertmanager.
"""

import copy
import json
import logging
from pathlib import Path

import pytest
import yaml
from fastapi.testclient import TestClient

from bridge.main import WEBHOOK_PATH, app

ROOT = Path(__file__).resolve().parent.parent
FIRING = json.loads((ROOT / "tests" / "fixtures" / "alertmanager_target_down_firing.json").read_text())
CATALOG = yaml.safe_load((ROOT / "service-catalog" / "ticket-api.yaml").read_text())


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        yield c


def payload(*alerts, status="firing"):
    body = copy.deepcopy(FIRING)
    body["status"] = status
    body["alerts"] = list(alerts) if alerts else body["alerts"]
    return body


def alert(**changes):
    a = copy.deepcopy(FIRING["alerts"][0])
    labels = changes.pop("labels", {})
    a["labels"].update(labels)
    a["labels"] = {k: v for k, v in a["labels"].items() if v is not None}
    a.update(changes)
    return a


def resolved(a):
    return {**a, "status": "resolved", "endsAt": "2026-10-08T20:24:15.416437123Z"}


def test_firing_target_down_is_enriched_from_the_catalog(client):
    resp = client.post(WEBHOOK_PATH, json=FIRING)
    assert resp.status_code == 200
    body = resp.json()
    assert (body["received"], body["enriched"], body["unmapped"], body["rejected"]) == (1, 1, 0, 0)

    r = body["results"][0]
    assert (r["status"], r["outcome"], r["alertname"]) == ("firing", "enriched", "TicketServiceTargetDown")
    assert r["fingerprint"] == "6c1b4e1f2b9d7a3e"
    # Affected target comes from the alert itself ...
    assert r["source"]["pod"] == "ticket-service-79b4b8b84d-f4qnp"
    assert r["source"]["instance"] == "10.244.0.10:8080"
    assert r["source"]["summary"].startswith("Prometheus cannot scrape")
    # ... operational context comes from the catalog, not from the alert.
    entry, defn = CATALOG, CATALOG["alerts"]["TicketServiceTargetDown"]
    inc = r["incident"]
    assert inc["application_owner"] == entry["owners"]["application"]
    assert inc["cloudops_owner"] == entry["owners"]["cloudops"]
    assert inc["dependencies"] == entry["dependencies"]
    assert inc["health_endpoint"] == entry["endpoints"]["health"]
    assert inc["readiness_endpoint"] == entry["endpoints"]["readiness"]
    assert inc["first_responder"] == defn["first_responder"]
    assert inc["runbook"] == defn["runbook"] == "ticket-service-target-down.md"
    assert inc["suggested_checks"] == defn["suggested_checks"]
    assert inc["severity"] == defn["severity"]["production"] == r["severity_label"]


def test_resolved_alert_keeps_identity_and_fingerprint(client):
    resp = client.post(WEBHOOK_PATH, json=payload(resolved(FIRING["alerts"][0]), status="resolved"))
    r = resp.json()["results"][0]
    assert resp.status_code == 200
    assert (r["status"], r["outcome"]) == ("resolved", "enriched")
    assert r["fingerprint"] == FIRING["alerts"][0]["fingerprint"]
    assert r["ends_at"] == "2026-10-08T20:24:15.416437123Z"


def test_mixed_firing_and_resolved_are_processed_per_alert(client):
    a = alert(fingerprint="aaa", labels={"pod": "pod-a"})
    b = resolved(alert(fingerprint="bbb", labels={"pod": "pod-b"}))
    resp = client.post(WEBHOOK_PATH, json=payload(a, b))  # group status "firing"
    results = {r["fingerprint"]: r for r in resp.json()["results"]}
    assert resp.status_code == 200
    assert results["aaa"]["status"] == "firing" and results["bbb"]["status"] == "resolved"
    assert results["aaa"]["source"]["pod"] == "pod-a" and results["bbb"]["source"]["pod"] == "pod-b"


def test_one_bad_alert_does_not_fail_the_batch(client):
    good = alert(fingerprint="good")
    bad = alert(fingerprint="bad", labels={"service": "payments"})
    gap = alert(fingerprint="gap", labels={"alertname": "DiskFull"})
    resp = client.post(WEBHOOK_PATH, json=payload(good, bad, gap))
    body = resp.json()
    assert resp.status_code == 200  # a 4xx/5xx would fail or retry the valid alert too
    assert (body["received"], body["enriched"], body["unmapped"], body["rejected"]) == (3, 1, 1, 1)
    outcomes = {r["fingerprint"]: r["outcome"] for r in body["results"]}
    assert outcomes == {"good": "enriched", "bad": "rejected", "gap": "unmapped"}


@pytest.mark.parametrize("label", ["alertname", "service", "environment"])
def test_missing_identity_label_is_rejected(client, label):
    r = client.post(WEBHOOK_PATH, json=payload(alert(labels={label: None}))).json()["results"][0]
    assert r["outcome"] == "rejected"
    assert label in r["reason"]
    assert r["incident"] is None


@pytest.mark.parametrize("labels, text", [
    ({"service": "payments"}, "not found in service catalog"),
    ({"environment": "moon"}, "Environment 'moon' is not defined"),
])
def test_unknown_service_or_environment_is_rejected(client, labels, text):
    r = client.post(WEBHOOK_PATH, json=payload(alert(labels=labels))).json()["results"][0]
    assert r["outcome"] == "rejected" and text in r["reason"] and r["incident"] is None


def test_unknown_alert_is_unmapped_documentation_gap(client):
    r = client.post(WEBHOOK_PATH, json=payload(alert(labels={"alertname": "DiskFull"}))).json()["results"][0]
    assert r["outcome"] == "unmapped"
    # No invented runbook, owners or checks: only the gap is reported.
    assert r["incident"] is None
    assert "Operational-readiness gap" in r["reason"] and "DiskFull" in r["reason"]
    assert r["source"]["pod"]  # what the alert itself said is still kept


def test_extra_and_future_alertmanager_fields_are_accepted(client):
    body = payload(alert(someFutureAlertField={"x": 1}))
    body["someFutureTopLevelField"] = "ok"
    assert "notification_reason" in body and "routeLabels" in body
    assert client.post(WEBHOOK_PATH, json=body).status_code == 200


@pytest.mark.parametrize("mutate", [
    lambda b: b.update(version="5"),            # unsupported format version
    lambda b: b.pop("alerts"),                  # required field missing
    lambda b: b["alerts"][0].update(status="pending"),
    lambda b: b["alerts"][0].pop("fingerprint"),
    lambda b: b["alerts"][0].update(labels="not-a-dict"),
])
def test_malformed_payload_returns_422(client, mutate):
    body = copy.deepcopy(FIRING)
    mutate(body)
    resp = client.post(WEBHOOK_PATH, json=body)
    assert resp.status_code == 422  # Alertmanager does not retry 4xx
    assert "Traceback" not in resp.text


def test_invalid_json_returns_422_without_traceback(client):
    resp = client.post(WEBHOOK_PATH, content="{not json", headers={"Content-Type": "application/json"})
    assert resp.status_code == 422 and "Traceback" not in resp.text


def test_catalog_not_loaded_returns_503_so_alertmanager_retries(client, monkeypatch):
    monkeypatch.delattr(client.app.state, "catalog")
    assert client.post(WEBHOOK_PATH, json=FIRING).status_code == 503


def test_high_error_rate_payload_is_compatible(client):
    # ISOLATED compatibility check only: HighErrorRate has never fired live
    # (the app has no safe way to produce 5xx).
    a = alert(labels={"alertname": "HighErrorRate", "severity": "critical",
                      "pod": None, "instance": None, "job": None, "namespace": None},
              annotations={"summary": "ticket-api is returning server errors (5xx)"})
    r = client.post(WEBHOOK_PATH, json=payload(a)).json()["results"][0]
    assert r["outcome"] == "enriched"
    assert r["incident"]["runbook"] == "high-error-rate.md"
    assert r["incident"]["severity"] == "critical"
    assert r["source"]["pod"] is None


def test_processing_is_logged_with_searchable_fields(client):
    records = []
    handler = logging.Handler()
    handler.emit = records.append
    logger = logging.getLogger("bridge")
    logger.addHandler(handler)
    try:
        client.post(WEBHOOK_PATH, json=FIRING)
    finally:
        logger.removeHandler(handler)
    lines = [r.getMessage() for r in records]
    assert any(l.startswith("event=webhook_received") and "alerts=1" in l for l in lines)
    processed = next(l for l in lines if l.startswith("event=alert_processed"))
    for part in ("status=firing", "outcome=enriched", "alertname=TicketServiceTargetDown",
                 "service=ticket-api", "environment=production", "pod=ticket-service-79b4b8b84d-f4qnp",
                 "runbook=ticket-service-target-down.md", "fingerprint=6c1b4e1f2b9d7a3e"):
        assert part in processed, part
    context = next(l for l in lines if l.startswith("event=incident_context"))
    defn = CATALOG["alerts"]["TicketServiceTargetDown"]
    for part in (f'application_owner="{CATALOG["owners"]["application"]}"',
                 f"cloudops_owner={CATALOG['owners']['cloudops']}",
                 f"first_responder={defn['first_responder']}",
                 f"dependencies={','.join(CATALOG['dependencies'])}",
                 f"checks={len(defn['suggested_checks'])}"):
        assert part in context, part


def test_manual_enrich_api_still_works_and_now_knows_target_down(client):
    resp = client.post("/incidents/enrich", json={"service": "ticket-api", "environment": "production",
                                                  "alert": "TicketServiceTargetDown"})
    assert resp.status_code == 200
    assert resp.json()["runbook"] == "ticket-service-target-down.md"
