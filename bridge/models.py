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
