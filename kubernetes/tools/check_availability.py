"""Poll a URL from inside the cluster and report how many requests failed.

Used during rollouts: `kubectl port-forward` pins one pod and breaks when
that pod is replaced, so availability has to be measured from inside the
cluster, through the Service, the way real clients reach it.

Standard library only, so it runs inside the existing app images:

  kubectl -n cloudops-bridge exec -i deploy/bridge -- \
    python - http://ticket-service:8080/ready 60 < kubernetes/tools/check_availability.py
"""

import sys
import time
import urllib.request

url = sys.argv[1]
duration = float(sys.argv[2]) if len(sys.argv) > 2 else 60
ok = failed = 0
deadline = time.time() + duration

while time.time() < deadline:
    try:
        with urllib.request.urlopen(url, timeout=2) as resp:
            ok += resp.status == 200
            failed += resp.status != 200
    except Exception as exc:
        failed += 1
        print(f"{time.strftime('%H:%M:%S')} FAILED: {exc}", flush=True)
    time.sleep(0.2)

print(f"{url}: {ok} ok, {failed} failed over {duration:.0f}s", flush=True)
sys.exit(1 if failed else 0)
