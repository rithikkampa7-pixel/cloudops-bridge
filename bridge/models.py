"""Pydantic schemas for the service catalog and the incident API."""

from typing import Dict, List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field

Severity = Literal["critical", "warning", "info"]
Responder = Literal["cloudops", "development"]


# ---- Service catalog (validated when the YAML is loaded) ----

class Owners(BaseModel):
    application: str
    cloudops: str


class Endpoints(BaseModel):
    health: str
    readiness: str
    metrics: Optional[str] = None


class AlertDefinition(BaseModel):
    summary: str
    severity: Dict[str, Severity]  # environment -> severity
    first_responder: Responder
    runbook: Optional[str] = None
    suggested_checks: List[str]


class ServiceEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")  # catch typos in catalog files

    service: str
    description: str
    tier: str
    owners: Owners
    environments: List[str]
    dependencies: List[str] = []
    endpoints: Endpoints
    alerts: Dict[str, AlertDefinition]


# ---- API request / response ----

class IncidentRequest(BaseModel):
    service: str = Field(..., min_length=1, max_length=100)
    environment: str = Field(..., min_length=1, max_length=50)
    alert: str = Field(..., min_length=1, max_length=100)


class EnrichedIncident(BaseModel):
    service: str
    environment: str
    alert: str
    summary: str
    severity: Severity
    first_responder: Responder
    application_owner: str
    cloudops_owner: str
    dependencies: List[str]
    health_endpoint: str
    readiness_endpoint: str
    runbook: Optional[str]
    suggested_checks: List[str]


# ---- Alertmanager webhook results (one per alert in a notification) ----

AlertStatus = Literal["firing", "resolved"]
# enriched: the catalog defines this service/environment/alert.
# unmapped: known service and environment, but the catalog has no entry for
#           this alert - an operational-readiness/documentation gap.
# rejected: the alert cannot be identified (missing label, unknown service,
#           or an environment the service does not run in).
Outcome = Literal["enriched", "unmapped", "rejected"]


class AlertSource(BaseModel):
    """What Prometheus/Alertmanager said about the affected target."""
    pod: Optional[str] = None
    instance: Optional[str] = None
    namespace: Optional[str] = None
    summary: Optional[str] = None
    description: Optional[str] = None
    generator_url: Optional[str] = None


class ProcessedAlert(BaseModel):
    status: AlertStatus
    outcome: Outcome
    reason: Optional[str] = None
    alertname: Optional[str] = None
    service: Optional[str] = None
    environment: Optional[str] = None
    severity_label: Optional[str] = None  # as labelled by the Prometheus rule
    fingerprint: str                      # same for an alert's firing and resolved events
    starts_at: str
    ends_at: str
    source: AlertSource
    incident: Optional[EnrichedIncident] = None  # only when outcome == "enriched"


class WebhookResult(BaseModel):
    receiver: str
    group_key: str
    group_status: AlertStatus
    received: int
    truncated_alerts: int
    enriched: int
    unmapped: int
    rejected: int
    results: List[ProcessedAlert]
