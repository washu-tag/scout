"""Tests for vendor_keycloak_operator: the release's kustomization.yml decides the file
set, and our kustomization's resources: list is rewritten without touching the rest.
"""

import pytest

from vendor_keycloak_operator import rewrite_resources, upstream_resources

UPSTREAM = """apiVersion: kustomize.config.k8s.io/v1beta1
kind: Kustomization

namespace: keycloak

resources:
  - keycloakoidcclients.k8s.keycloak.org-v1.yml
  - keycloaks.k8s.keycloak.org-v1.yml
  - kubernetes.yml

transformers:
  - |-
    apiVersion: builtin
    kind: NamespaceTransformer
"""

OURS = """apiVersion: kustomize.config.k8s.io/v1beta1
kind: Kustomization
# comments and the namespace stay as they are
namespace: '${keycloak_namespace}'
resources:
  - upstream/keycloaks.k8s.keycloak.org-v1.yml
  - upstream/kubernetes.yml
"""


def test_upstream_resources_reads_only_the_resources_list():
    assert upstream_resources(UPSTREAM) == [
        "keycloakoidcclients.k8s.keycloak.org-v1.yml",
        "keycloaks.k8s.keycloak.org-v1.yml",
        "kubernetes.yml",
    ]


@pytest.mark.parametrize("entry", ["../escape.yml", "sub/dir.yml", "https://x/y.yml"])
def test_upstream_resources_rejects_anything_but_file_names(entry):
    with pytest.raises(ValueError):
        upstream_resources(f"resources:\n  - {entry}\n")


def test_rewrite_resources_replaces_only_the_list():
    out = rewrite_resources(OURS, ["new.k8s.keycloak.org-v1.yml", "kubernetes.yml"])
    assert out == OURS.replace(
        "  - upstream/keycloaks.k8s.keycloak.org-v1.yml\n",
        "  - upstream/new.k8s.keycloak.org-v1.yml\n",
    )
