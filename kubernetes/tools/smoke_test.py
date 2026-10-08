"""Check that the application really works, through the Kubernetes Services.

Run after every failure, rollout, or rollback exercise. "Running" pods are
not proof the app works; this exercises the real endpoints.

Standard library only, so it runs inside the existing app images:

  kubectl -n cloudops-bridge exec -i deploy/bridge -- python - < kubernetes/tools/smoke_test.py
"""

import json
import sys
import urllib.request

TICKET = "http://ticket-service:8080"
BRIDGE = "http://bridge:8081"
INCIDENT = {"service": "ticket-api", "environment": "production", "alert": "HighErrorRate"}


def call(url, body=None):
    req = urllib.request.Request(
        url,
        data=json.dumps(body).encode() if body else None,
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=3) as resp:
            return resp.status, json.load(resp)
    except urllib.error.HTTPError as exc:
        return exc.code, None
    except Exception as exc:
        return None, str(exc)


checks = [
    ("ticket-service /health", call(f"{TICKET}/health"), 200),
    ("ticket-service /ready", call(f"{TICKET}/ready"), 200),
    ("ticket-service /tickets", call(f"{TICKET}/tickets"), 200),
    ("bridge /health", call(f"{BRIDGE}/health"), 200),
    ("bridge /ready", call(f"{BRIDGE}/ready"), 200),
    ("bridge enrich", call(f"{BRIDGE}/incidents/enrich", INCIDENT), 200),
    ("bridge unknown service", call(f"{BRIDGE}/incidents/enrich", {**INCIDENT, "service": "payments"}), 404),
    ("bridge invalid input", call(f"{BRIDGE}/incidents/enrich", {"service": "ticket-api"}), 422),
]

failed = 0
for name, (status, _), expected in checks:
    ok = status == expected
    failed += not ok
    print(f"{'PASS' if ok else 'FAIL'}  {name}: {status} (expected {expected})")

enriched = checks[5][1][1]
if isinstance(enriched, dict):
    print(f"      enrich -> severity={enriched['severity']} owner={enriched['application_owner']} runbook={enriched['runbook']}")

print("ALL PASSED" if not failed else f"{failed} FAILED")
sys.exit(1 if failed else 0)
