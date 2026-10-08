"""Query Prometheus from your Mac and print compact results.

Needs a port-forward first:
  kubectl -n monitoring port-forward svc/prometheus 19090:9090

Usage:
  python3 kubernetes/tools/promql.py 'up{job="ticket-service"}'
  python3 kubernetes/tools/promql.py --targets
  python3 kubernetes/tools/promql.py --rules

Standard library only. Prometheus's raw JSON is verbose; this prints one line
per series (labels and value) so results are easy to read and compare.
"""

import json
import os
import sys
import urllib.parse
import urllib.request

PROM = os.environ.get("PROM_URL", "http://localhost:19090")


def get(path, **params):
    url = f"{PROM}{path}?{urllib.parse.urlencode(params)}"
    with urllib.request.urlopen(url, timeout=5) as resp:
        body = json.load(resp)
    if body.get("status") != "success":
        sys.exit(f"Prometheus error: {body}")
    return body["data"]


if len(sys.argv) != 2:
    sys.exit(__doc__)

if sys.argv[1] == "--targets":
    for t in get("/api/v1/targets", state="active")["activeTargets"]:
        labels = t["labels"]
        who = labels.get("pod", labels.get("instance"))
        error = f"  error={t['lastError']}" if t["lastError"] else ""
        print(f"{labels['job']:15} {who:34} {t['scrapeUrl']:38} {t['health'].upper():5}{error}")
    sys.exit(0)

if sys.argv[1] == "--rules":
    for group in get("/api/v1/rules", type="alert")["groups"]:
        print(f"group {group['name']}  file={group['file']}  interval={group['interval']}s")
        for rule in group["rules"]:
            labels = ",".join(f"{k}={v}" for k, v in sorted(rule["labels"].items()))
            print(f"  {rule['name']:24} state={rule['state']:8} health={rule['health']}  "
                  f"for={rule['duration']}s  labels: {labels}")
            print(f"  {'':24} expr: {' '.join(rule['query'].split())}")
            for alert in rule.get("alerts", []):
                who = alert["labels"].get("pod", "")
                print(f"  {'':24} -> {alert['state'].upper()} {who} since {alert.get('activeAt', '')[:19]}")
    sys.exit(0)

data = get("/api/v1/query", query=sys.argv[1])
if not data["result"]:
    print("(no data)")
for series in data["result"]:
    labels = {k: v for k, v in series.get("metric", {}).items() if k != "__name__"}
    name = series.get("metric", {}).get("__name__", "")
    shown = ",".join(f'{k}="{v}"' for k, v in sorted(labels.items()))
    print(f"{name}{{{shown}}}  {series['value'][1]}")
