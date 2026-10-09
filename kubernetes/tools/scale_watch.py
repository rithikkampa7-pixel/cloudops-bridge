"""Print one line per poll describing the ticket-service autoscaling state.

Reads only the Kubernetes API (via kubectl), no port-forward needed:
  - HPA: current/desired replicas and its CPU utilization (from metrics-server)
  - Deployment: ready/available replicas
  - per-pod CPU from the metrics API (metrics.k8s.io), shown as m and % of request

Usage:
  python3 kubernetes/tools/scale_watch.py 1200          # seconds to watch
  python3 kubernetes/tools/scale_watch.py 1200 5        # poll every 5s (default)
"""

import json
import subprocess
import sys
import time

NS = "cloudops-bridge"
duration = float(sys.argv[1]) if len(sys.argv) > 1 else 600
every = float(sys.argv[2]) if len(sys.argv) > 2 else 5


def kubectl_json(*args):
    out = subprocess.run(["kubectl", "-n", NS, *args, "-o", "json"], capture_output=True, text=True)
    return json.loads(out.stdout) if out.returncode == 0 and out.stdout else {}


def raw(path):
    out = subprocess.run(["kubectl", "get", "--raw", path], capture_output=True, text=True)
    return json.loads(out.stdout) if out.returncode == 0 and out.stdout else {}


def millicores(value):
    if value.endswith("n"):
        return int(value[:-1]) / 1e6
    if value.endswith("u"):
        return int(value[:-1]) / 1e3
    if value.endswith("m"):
        return float(value[:-1])
    return float(value) * 1000


start = time.time()
while time.time() - start < duration:
    hpa = kubectl_json("get", "hpa", "ticket-service")
    st = hpa.get("status", {})
    util = next((m["resource"]["current"].get("averageUtilization")
                 for m in st.get("currentMetrics") or [] if m.get("type") == "Resource"), None)
    dep = kubectl_json("get", "deployment", "ticket-service").get("status", {})
    pods = raw(f"/apis/metrics.k8s.io/v1beta1/namespaces/{NS}/pods").get("items", [])
    cpu = []
    for p in sorted(pods, key=lambda p: p["metadata"]["name"]):
        if not p["metadata"]["name"].startswith("ticket-service-"):
            continue
        m = sum(millicores(c["usage"]["cpu"]) for c in p["containers"])
        cpu.append(f"{p['metadata']['name'][-5:]}={m:.0f}m({m / 50 * 100:.0f}%)")
    print(f"{time.strftime('%H:%M:%S')} +{time.time() - start:5.0f}s | "
          f"hpa current={st.get('currentReplicas')} desired={st.get('desiredReplicas')} "
          f"cpu={util}%/70% | deploy ready={dep.get('readyReplicas')} available={dep.get('availableReplicas')} | "
          f"metrics-api {' '.join(cpu) or '-'}", flush=True)
    time.sleep(every)
