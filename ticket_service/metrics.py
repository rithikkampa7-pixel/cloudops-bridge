"""Minimal metrics in the Prometheus text exposition format.

No Prometheus server or client library is used in Phase 1. The output format
is what Prometheus expects to scrape, so a later phase can point Prometheus at
GET /metrics without changing this endpoint.
"""

import threading
from collections import Counter

_lock = threading.Lock()
_requests: Counter = Counter()  # key: (method, path, status_code)


def record_request(method: str, path: str, status_code: int) -> None:
    with _lock:
        _requests[(method, path, str(status_code))] += 1


def reset() -> None:
    with _lock:
        _requests.clear()


def render(tickets_available: int, tickets_sold: int) -> str:
    lines = [
        "# HELP http_requests_total Total HTTP requests handled.",
        "# TYPE http_requests_total counter",
    ]
    with _lock:
        for (method, path, status), count in sorted(_requests.items()):
            lines.append(
                f'http_requests_total{{method="{method}",path="{path}",status="{status}"}} {count}'
            )
    lines += [
        "# HELP tickets_available Tickets currently available for purchase.",
        "# TYPE tickets_available gauge",
        f"tickets_available {tickets_available}",
        "# HELP tickets_sold_total Tickets sold since the service started.",
        "# TYPE tickets_sold_total counter",
        f"tickets_sold_total {tickets_sold}",
    ]
    return "\n".join(lines) + "\n"
