"""GitOps: the Argo CD install script, AppProject and Application.

These pin the decisions in gitops/README.md: one repository and branch,
ticket-service only, manual sync without pruning, no Secrets or
cluster-scoped resources, and the HPA as the only owner of the replica count.
"""

import re
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
GITOPS = ROOT / "gitops"
REPO_URL = "https://github.com/rithikkampa7-pixel/cloudops-bridge.git"


def load_all(path):
    return [d for d in yaml.safe_load_all(path.read_text()) if d]


PROJECT = load_all(GITOPS / "appproject.yaml")[0]
APP = load_all(GITOPS / "ticket-service-application.yaml")[0]
DEPLOYMENT = load_all(ROOT / "kubernetes" / "ticket-service" / "deployment.yaml")[0]
INSTALL = (GITOPS / "install-argocd.sh").read_text()


# ---- Application ----

def test_application_deploys_ticket_service_from_this_repo_and_branch():
    source = APP["spec"]["source"]
    assert APP["kind"] == "Application" and APP["metadata"]["namespace"] == "argocd"
    assert source["repoURL"] == REPO_URL
    assert source["targetRevision"] == "phase8-gitops"
    assert source["path"] == "kubernetes/ticket-service"
    assert source.get("directory", {}).get("recurse", False) is False
    assert APP["spec"]["project"] == PROJECT["metadata"]["name"]


def test_application_path_holds_only_ticket_service_resources():
    kinds = sorted(d["kind"] for p in (ROOT / "kubernetes" / "ticket-service").glob("*.yaml")
                   for d in load_all(p))
    assert kinds == ["Deployment", "HorizontalPodAutoscaler", "Service"]


def test_sync_is_automated_with_self_heal_and_never_prunes():
    policy = APP["spec"]["syncPolicy"]
    # Argo CD applies Git changes on its own and reverts live drift...
    assert policy["automated"]["enabled"] is True
    assert policy["automated"]["selfHeal"] is True
    # ...but never deletes a live resource because its file left Git.
    assert policy["automated"]["prune"] is False
    assert "Prune=true" not in policy.get("syncOptions", [])
    assert not policy["automated"].get("allowEmpty", False)


def test_application_does_not_create_namespace_or_cascade_delete():
    options = APP["spec"]["syncPolicy"]["syncOptions"]
    assert "CreateNamespace=false" in options
    assert "CreateNamespace=true" not in options
    # Without the resources finalizer, deleting the Application keeps the service running.
    assert not APP["metadata"].get("finalizers")


# ---- HPA safeguards ----

def test_deployment_has_no_replicas_field():
    assert "replicas" not in DEPLOYMENT["spec"]


def test_argocd_ignores_replica_differences_during_diff_and_sync():
    (rule,) = APP["spec"]["ignoreDifferences"]
    assert (rule["group"], rule["kind"], rule["name"]) == ("apps", "Deployment", "ticket-service")
    assert rule["jsonPointers"] == ["/spec/replicas"]
    assert "RespectIgnoreDifferences=true" in APP["spec"]["syncPolicy"]["syncOptions"]


def test_deployment_reports_stuck_rollouts_after_120s_without_changing_strategy():
    assert DEPLOYMENT["spec"]["progressDeadlineSeconds"] == 120
    assert DEPLOYMENT["spec"]["strategy"]["rollingUpdate"] == {"maxUnavailable": 0, "maxSurge": 1}


# ---- AppProject ----

def test_project_allows_only_this_repo_and_namespace():
    assert PROJECT["kind"] == "AppProject" and PROJECT["metadata"]["namespace"] == "argocd"
    assert PROJECT["spec"]["sourceRepos"] == [REPO_URL]
    assert PROJECT["spec"]["destinations"] == [
        {"server": "https://kubernetes.default.svc", "namespace": "cloudops-bridge"}
    ]
    dest = APP["spec"]["destination"]
    assert (dest["server"], dest["namespace"]) == ("https://kubernetes.default.svc", "cloudops-bridge")


def test_project_allows_no_cluster_scoped_resources():
    assert PROJECT["spec"]["clusterResourceWhitelist"] == []


def test_project_allows_only_ticket_service_kinds_and_no_secrets():
    allowed = {(r["group"], r["kind"]) for r in PROJECT["spec"]["namespaceResourceWhitelist"]}
    assert allowed == {("apps", "Deployment"), ("", "Service"),
                       ("autoscaling", "HorizontalPodAutoscaler")}
    assert not any(kind == "Secret" or kind == "*" or group == "*" for group, kind in allowed)


def test_no_secrets_in_gitops_manifests():
    docs = [d for p in sorted(GITOPS.glob("*.yaml")) for d in load_all(p)]
    assert {d["kind"] for d in docs} == {"AppProject", "Application"}


# ---- install script ----

def test_install_script_pins_and_verifies_an_official_argocd_3_release():
    version = re.search(r'^ARGOCD_VERSION="(.+)"$', INSTALL, re.M).group(1)
    sha = re.search(r'^ARGOCD_INSTALL_SHA256="(.+)"$', INSTALL, re.M).group(1)
    url = re.search(r'^ARGOCD_INSTALL_URL="(.+)"$', INSTALL, re.M).group(1)
    assert re.fullmatch(r"v3\.\d+\.\d+", version)
    assert re.fullmatch(r"[0-9a-f]{64}", sha)
    assert url == "https://raw.githubusercontent.com/argoproj/argo-cd/${ARGOCD_VERSION}/manifests/install.yaml"
    # The checksum comparison happens before anything is applied.
    assert INSTALL.index('!= "$ARGOCD_INSTALL_SHA256"') < INSTALL.index("kubectl apply --namespace")


def test_install_script_never_reads_the_admin_password():
    assert "argocd-initial-admin-secret" not in INSTALL
    assert "pbcopy" not in INSTALL
