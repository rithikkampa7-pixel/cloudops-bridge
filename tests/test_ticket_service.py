import pytest
from fastapi.testclient import TestClient

from ticket_service import metrics
from ticket_service.inventory import INITIAL_INVENTORY, inventory
from ticket_service.main import app

client = TestClient(app)


@pytest.fixture(autouse=True)
def fresh_state():
    """Each test starts with full inventory and zeroed metrics."""
    inventory.reset()
    metrics.reset()
    yield


def test_health():
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "healthy"}


def test_ready():
    resp = client.get("/ready")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ready"}


def test_get_tickets():
    resp = client.get("/tickets")
    assert resp.status_code == 200
    assert resp.json() == {
        "event": "Adventure Park General Admission",
        "price": 49.99,
        "available": INITIAL_INVENTORY,
    }


def test_purchase_success_decrements_inventory():
    resp = client.post("/tickets/purchase", json={"quantity": 2})
    assert resp.status_code == 201
    body = resp.json()
    assert body["status"] == "confirmed"
    assert body["quantity"] == 2
    assert body["total_price"] == 99.98
    assert body["remaining"] == INITIAL_INVENTORY - 2

    assert client.get("/tickets").json()["available"] == INITIAL_INVENTORY - 2


@pytest.mark.parametrize(
    "payload",
    [
        {"quantity": 0},
        {"quantity": -1},
        {"quantity": 11},          # above per-order limit
        {"quantity": "two"},
        {"quantity": 1.5},
        {},                        # missing field
    ],
)
def test_purchase_invalid_quantity(payload):
    resp = client.post("/tickets/purchase", json=payload)
    assert resp.status_code == 422
    assert client.get("/tickets").json()["available"] == INITIAL_INVENTORY


def test_purchase_more_than_available_returns_409():
    inventory._available = 3  # simulate a nearly sold-out event
    resp = client.post("/tickets/purchase", json={"quantity": 5})
    assert resp.status_code == 409
    assert "only 3 available" in resp.json()["detail"]
    assert inventory.available == 3


def test_metrics_prometheus_format():
    client.post("/tickets/purchase", json={"quantity": 2})
    resp = client.get("/metrics")
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/plain")
    text = resp.text
    assert "# TYPE http_requests_total counter" in text
    assert 'http_requests_total{method="POST",path="/tickets/purchase",status="201"} 1' in text
    assert f"tickets_available {INITIAL_INVENTORY - 2}" in text
    assert "tickets_sold_total 2" in text
