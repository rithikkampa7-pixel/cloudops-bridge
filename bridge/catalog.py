"""Loads the YAML service catalog into validated ServiceEntry objects."""

import os
from pathlib import Path
from typing import Dict

import yaml

from bridge.models import ServiceEntry

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
