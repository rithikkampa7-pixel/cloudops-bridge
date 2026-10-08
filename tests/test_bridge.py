import pytest
from fastapi.testclient import TestClient

from bridge.catalog import CatalogError, catalog_dir, load_catalog, runbook_dir
from bridge.main import app

VALID_INCIDENT = {
    "service": "ticket-api",
    "environment": "production",
    "alert": "HighErrorRate",
}


@pytest.fixture(scope="module")
def client():
    # Using the context manager runs the startup (lifespan) that loads the catalog.
    with TestClient(app) as c:
        yield c


def test_enrich_high_error_rate(client):
    resp = client.post("/incidents/enrich", json=VALID_INCIDENT)
    assert resp.status_code == 200
    body = resp.json()
    assert body["service"] == "ticket-api"
    assert body["environment"] == "production"
    assert body["alert"] == "HighErrorRate"
    assert body["severity"] == "critical"
    assert body["application_owner"] == "Ticket Development"
    assert body["cloudops_owner"] == "CloudOps"
    assert body["dependencies"] == ["postgresql"]
    assert body["runbook"] == "high-error-rate.md"
    assert body["first_responder"] == "cloudops"
    assert "Check recent deployments" in body["suggested_checks"]


def test_severity_depends_on_environment(client):
    resp = client.post("/incidents/enrich", json={**VALID_INCIDENT, "environment": "staging"})
    assert resp.status_code == 200
    assert resp.json()["severity"] == "warning"


def test_alert_without_runbook_flags_gap(client):
    resp = client.post("/incidents/enrich", json={**VALID_INCIDENT, "alert": "HighLatency"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["runbook"] is None
    assert any("No runbook exists" in c for c in body["suggested_checks"])


def test_unknown_service_returns_404(client):
    resp = client.post("/incidents/enrich", json={**VALID_INCIDENT, "service": "no-such-service"})
    assert resp.status_code == 404
    assert "not found" in resp.json()["detail"]


def test_unknown_environment_returns_422(client):
    resp = client.post("/incidents/enrich", json={**VALID_INCIDENT, "environment": "moon"})
    assert resp.status_code == 422


def test_unknown_alert_returns_422(client):
    resp = client.post("/incidents/enrich", json={**VALID_INCIDENT, "alert": "DiskFull"})
    assert resp.status_code == 422


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"service": "ticket-api", "environment": "production"},    # missing alert
        {"service": "", "environment": "production", "alert": "HighErrorRate"},
        {"service": 123, "environment": "production", "alert": "HighErrorRate"},
    ],
)
def test_missing_or_invalid_fields_return_422(client, payload):
    resp = client.post("/incidents/enrich", json=payload)
    assert resp.status_code == 422


def test_malformed_json_returns_422_without_traceback(client):
    resp = client.post(
        "/incidents/enrich",
        content="{not json",
        headers={"Content-Type": "application/json"},
    )
    assert resp.status_code == 422
    assert "Traceback" not in resp.text


# ---- Catalog integrity: catches broken handoff data before deploy ----

def test_every_catalog_runbook_exists():
    for entry in load_catalog(catalog_dir()).values():
        for name, alert in entry.alerts.items():
            if alert.runbook:
                assert (runbook_dir() / alert.runbook).is_file(), (
                    f"{entry.service}/{name} references missing runbook {alert.runbook}"
                )


def test_invalid_catalog_fails_fast(tmp_path):
    (tmp_path / "broken.yaml").write_text("service: x\n")  # missing required fields
    with pytest.raises(CatalogError):
        load_catalog(tmp_path)
