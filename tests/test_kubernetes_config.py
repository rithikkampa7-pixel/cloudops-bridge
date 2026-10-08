"""The ConfigMaps are generated from service-catalog/, runbooks/ and dashboards/.

These tests fail if someone edits a source file without running
kubernetes/generate-configmaps.sh, so Kubernetes can never silently run
an older catalog or runbook than the one in the repo.
"""

from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parent.parent


@pytest.mark.parametrize(
    "configmap, source_dir, pattern",
    [
        ("config/service-catalog-configmap.yaml", "service-catalog", "*.yaml"),
        ("config/runbooks-configmap.yaml", "runbooks", "*.md"),
        ("monitoring/grafana/dashboard-configmap.yaml", "dashboards", "*.json"),
    ],
)
def test_configmap_matches_source(configmap, source_dir, pattern):
    manifest = yaml.safe_load((ROOT / "kubernetes" / configmap).read_text())
    expected = {p.name: p.read_text() for p in sorted((ROOT / source_dir).glob(pattern))}
    assert manifest["data"] == expected, (
        f"{configmap} is out of date: run ./kubernetes/generate-configmaps.sh"
    )
