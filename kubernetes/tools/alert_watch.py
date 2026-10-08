"""Print one timeline line per poll: Kubernetes, Prometheus and Alertmanager.

Used to observe an alert's real lifecycle (inactive -> pending -> firing ->
resolved) and when Alertmanager has it. Needs two port-forwards:
  kubectl -n monitoring port-forward svc/prometheus 19090:9090
  kubectl -n monitoring port-forward svc/alertmanager 19093:9093

Usage:
  python3 kubernetes/tools/alert_watch.py 120
  python3 kubernetes/tools/alert_watch.py 120 POD_NAME

Each line shows: elapsed seconds; pod READY/restarts (if a pod is given);
up per ticket-service pod; Prometheus alert states; Alertmanager's active alerts.
Standard library only (calls kubectl for pod status).
"""

import json
import os
import subprocess
import sys
import time
import urllib.request

PROM = os.environ.get("PROM_URL", "http://localhost:19090")
AM = os.environ.get("AM_URL", "http://localhost:19093")
duration = float(sys.argv[1]) if len(sys.argv) > 1 else 120
pod = sys.argv[2] if len(sys.argv) > 2 else None


def get(url):
    try:
        with urllib.request.urlopen(url, timeout=3) as resp:
            return json.load(resp)
    except Exception as exc:
        return {"error": str(exc)}


def short(name):
    return name.rsplit("-", 1)[-1] if name else "?"


def k8s(pod):
    out = subprocess.run(
        ["kubectl", "-n", "cloudops-bridge", "get", "pod", pod, "--no-headers"],
        capture_output=True, text=True).stdout.split()
    return f"k8s {out[1]} r={out[3]}" if len(out) >= 4 else "k8s gone"


start = time.time()
while time.time() - start < duration:
    ups = get(f"{PROM}/api/v1/query?query=up%7Bjob%3D%22ticket-service%22%7D")
    up = " ".join(f"{short(s['metric'].get('pod'))}={s['value'][1]}"
                  for s in ups.get("data", {}).get("result", []))
    alerts = get(f"{PROM}/api/v1/alerts").get("data", {}).get("alerts", [])
    prom = " ".join(f"{a['labels']['alertname']}[{short(a['labels'].get('pod', ''))}]={a['state'].upper()}"
                    for a in alerts) or "none"
    am_alerts = get(f"{AM}/api/v2/alerts?active=true")
    am = " ".join(f"{a['labels']['alertname']}[{short(a['labels'].get('pod', ''))}]={a['status']['state']}"
                  for a in am_alerts) if isinstance(am_alerts, list) else "unreachable"
    parts = [f"{time.strftime('%H:%M:%S')} +{time.time() - start:4.0f}s"]
    if pod:
        parts.append(k8s(pod))
    parts += [f"up: {up or '-'}", f"prometheus: {prom}", f"alertmanager: {am or 'none'}"]
    print(" | ".join(parts), flush=True)
    time.sleep(3)
