"""Loads the YAML service catalog into validated ServiceEntry objects."""

import os
from pathlib import Path
from typing import Dict

import yaml

from bridge.models import EnrichedIncident, ServiceEntry

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CATALOG_DIR = REPO_ROOT / "service-catalog"
DEFAULT_RUNBOOK_DIR = REPO_ROOT / "runbooks"


class CatalogError(Exception):
    """Raised when a catalog file is missing, malformed, or inconsistent."""


def catalog_dir() -> Path:
    return Path(os.environ.get("CATALOG_DIR", DEFAULT_CATALOG_DIR))


def runbook_dir() -> Path:
    return Path(os.environ.get("RUNBOOK_DIR", DEFAULT_RUNBOOK_DIR))


def load_catalog(directory: Path) -> Dict[str, ServiceEntry]:
    """Read every *.yaml file in `directory`, keyed by service name.

    Fails fast on bad data so a broken catalog is caught at startup,
    not in the middle of an incident.
    """
    if not directory.is_dir():
        raise CatalogError(f"Catalog directory not found: {directory}")

    services: Dict[str, ServiceEntry] = {}
    for path in sorted(directory.glob("*.yaml")):
        try:
            raw = yaml.safe_load(path.read_text())
            entry = ServiceEntry.model_validate(raw)
        except Exception as exc:
            raise CatalogError(f"Invalid catalog file {path.name}: {exc}") from exc

        if entry.service in services:
            raise CatalogError(f"Duplicate service '{entry.service}' in {path.name}")
        services[entry.service] = entry

    if not services:
        raise CatalogError(f"No service catalog files found in {directory}")
    return services


class LookupFailure(Exception):
    """A service/environment/alert that the catalog does not define.

    `kind` says which part failed; `status_code` is what the manual
    /incidents/enrich API has always returned for it.
    """

    def __init__(self, kind: str, status_code: int, detail: str):
        super().__init__(detail)
        self.kind = kind
        self.status_code = status_code
        self.detail = detail


def enrich(catalog: Dict[str, ServiceEntry], service: str, environment: str,
           alert_name: str) -> EnrichedIncident:
    """Build an enriched incident purely from the catalog.

    Shared by POST /incidents/enrich and the Alertmanager webhook so both
    always return identical operational context for the same alert.
    """
    entry = catalog.get(service)
    if entry is None:
        raise LookupFailure("unknown_service", 404,
                            f"Service '{service}' not found in service catalog")

    if environment not in entry.environments:
        raise LookupFailure(
            "unknown_environment", 422,
            f"Environment '{environment}' is not defined for "
            f"'{entry.service}'. Known: {entry.environments}",
        )

    alert = entry.alerts.get(alert_name)
    if alert is None:
        raise LookupFailure(
            "unknown_alert", 422,
            f"Alert '{alert_name}' is not defined for '{entry.service}'. "
            f"Known: {sorted(entry.alerts)}",
        )

    checks = list(alert.suggested_checks)
    if alert.runbook is None:
        checks.append(
            "No runbook exists for this alert: document the resolution "
            "in runbooks/ afterward"
        )

    return EnrichedIncident(
        service=entry.service,
        environment=environment,
        alert=alert_name,
        summary=alert.summary,
        # Fall back to "warning" if the catalog doesn't list a severity for this environment.
        severity=alert.severity.get(environment, "warning"),
        first_responder=alert.first_responder,
        application_owner=entry.owners.application,
        cloudops_owner=entry.owners.cloudops,
        dependencies=entry.dependencies,
        health_endpoint=entry.endpoints.health,
        readiness_endpoint=entry.endpoints.readiness,
        runbook=alert.runbook,
        suggested_checks=checks,
    )
