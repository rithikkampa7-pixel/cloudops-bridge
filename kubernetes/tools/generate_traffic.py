"""Send a small, deterministic mix of traffic to ticket-service through its Service.

Not a load test (Locust comes later). The mix is fixed so the printed totals
can be compared exactly with Prometheus counters:

  every request      GET  /tickets                     -> 200
  every 10th request POST /tickets/purchase quantity=2 -> 201  (2 tickets)
  every 20th request POST /tickets/purchase quantity=0 -> 422  (expected 4xx)
  every 25th request GET  /no-such-page                -> 404  (expected 4xx)

Standard library only, so it runs inside the existing app images:

  kubectl -n cloudops-bridge exec -i deploy/bridge -- python - 600 < kubernetes/tools/generate_traffic.py
"""

import json
import sys
import time
import urllib.error
import urllib.request

BASE = "http://ticket-service:8080"
total = int(sys.argv[1]) if len(sys.argv) > 1 else 600
per_second = 10
counts = {"GET /tickets 200": 0, "POST purchase 201": 0, "POST purchase 422": 0,
          "GET /no-such-page 404": 0, "unexpected": 0}
tickets_bought = 0


def call(method, path, body=None):
    req = urllib.request.Request(
        BASE + path, method=method,
        data=json.dumps(body).encode() if body is not None else None,
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=3) as resp:
            return resp.status
    except urllib.error.HTTPError as exc:
        return exc.code
    except Exception:
        return None


start = time.time()
for i in range(1, total + 1):
    counts["GET /tickets 200" if call("GET", "/tickets") == 200 else "unexpected"] += 1
    if i % 10 == 0:
        ok = call("POST", "/tickets/purchase", {"quantity": 2}) == 201
        counts["POST purchase 201" if ok else "unexpected"] += 1
        tickets_bought += 2 if ok else 0
    if i % 20 == 0:
        ok = call("POST", "/tickets/purchase", {"quantity": 0}) == 422
        counts["POST purchase 422" if ok else "unexpected"] += 1
    if i % 25 == 0:
        ok = call("GET", "/no-such-page") == 404
        counts["GET /no-such-page 404" if ok else "unexpected"] += 1
    time.sleep(max(0, start + i / per_second - time.time()))

print(f"sent over {time.time() - start:.0f}s:")
for name, n in counts.items():
    print(f"  {name:24} {n}")
print(f"  tickets bought           {tickets_bought}")
sys.exit(1 if counts["unexpected"] else 0)
