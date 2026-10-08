"""Alertmanager webhook: payload schema and per-alert processing.

The payload is Alertmanager's own webhook format (version "4", checked against
the v0.34.1 source). It is an external schema we do not control and that
gains fields over time (e.g. notification_reason, routeLabels), so unknown
fields are ignored here - unlike the service catalog, which we own and where
extra="forbid" catches typos.

Each alert in a notification is processed on its own: one notification can
mix firing and resolved alerts, and one bad alert must not hide the others.
"""

from typing import Dict, List, Literal

from pydantic import BaseModel, ConfigDict

from bridge.catalog import LookupFailure, enrich
from bridge.models import AlertSource, ProcessedAlert, ServiceEntry

REQUIRED_LABELS = ("alertname", "service", "environment")


class AlertmanagerAlert(BaseModel):
    model_config = ConfigDict(extra="ignore")

    status: Literal["firing", "resolved"]
    labels: Dict[str, str]
    annotations: Dict[str, str] = {}
    # Kept as Alertmanager's strings (RFC 3339, nanosecond precision; a
    # firing alert's endsAt may be Go's zero time). Passed through untouched.
    startsAt: str
    endsAt: str
    generatorURL: str = ""
    fingerprint: str


class AlertmanagerWebhook(BaseModel):
    model_config = ConfigDict(extra="ignore")

    version: Literal["4"]  # refuse a future incompatible format instead of misreading it
    groupKey: str
    truncatedAlerts: int = 0
    status: Literal["firing", "resolved"]
    receiver: str
    groupLabels: Dict[str, str] = {}
    commonLabels: Dict[str, str] = {}
    commonAnnotations: Dict[str, str] = {}
    externalURL: str = ""
    alerts: List[AlertmanagerAlert]


def process_alert(alert: AlertmanagerAlert, catalog: Dict[str, ServiceEntry]) -> ProcessedAlert:
    """Map one alert to the catalog by its stable labels only.

    Never by pod, IP, fingerprint or annotation text: those change.
    """
    labels, annotations = alert.labels, alert.annotations
    result = dict(
        status=alert.status,
        alertname=labels.get("alertname"),
        service=labels.get("service"),
        environment=labels.get("environment"),
        severity_label=labels.get("severity"),
        fingerprint=alert.fingerprint,
        starts_at=alert.startsAt,
        ends_at=alert.endsAt,
        source=AlertSource(
            pod=labels.get("pod"),
            instance=labels.get("instance"),
            namespace=labels.get("namespace"),
            summary=annotations.get("summary"),
            description=annotations.get("description"),
            generator_url=alert.generatorURL or None,
        ),
    )

    missing = [name for name in REQUIRED_LABELS if not labels.get(name)]
    if missing:
        return ProcessedAlert(outcome="rejected",
                              reason=f"missing required label(s): {', '.join(missing)}", **result)

    try:
        incident = enrich(catalog, labels["service"], labels["environment"], labels["alertname"])
    except LookupFailure as exc:
        if exc.kind == "unknown_alert":
            return ProcessedAlert(
                outcome="unmapped",
                reason=(
                    f"Operational-readiness gap: alert '{labels['alertname']}' has no entry "
                    f"for service '{labels['service']}' in the service catalog, so there is "
                    f"no owner response, suggested checks or runbook for it. Add it to "
                    f"service-catalog/{labels['service']}.yaml."
                ),
                **result,
            )
        return ProcessedAlert(outcome="rejected", reason=exc.detail, **result)

    return ProcessedAlert(outcome="enriched", incident=incident, **result)
