"""CI wait/suspend policies must select complete production dependency closures."""

import copy
import os
import shutil
import subprocess

import pytest
import yaml

import check_legs as legs


@pytest.fixture
def graph():
    by_set = {
        path: legs.kustomizations(directory) for path, directory in legs.SETS.items()
    }
    return by_set, legs.dependency_graph(by_set)


def test_both_current_legs_select_their_exact_dependency_closure(graph):
    by_set, deps = graph
    ingest = legs.validate_leg("ingest", legs.LEGS["ingest"], by_set, deps)
    auth = legs.validate_leg("auth", legs.LEGS["auth"], by_set, deps)
    assert len(ingest) == 16
    assert len(auth) == 22
    assert set(deps) - auth == {
        "extractor",
        "temporal-server",
        "temporal-bootstrap",
        "trino-rw",
        "superset-server",
        "superset-dashboards",
    }
    assert set(legs.LEGS["auth"]["entrypoints"]) <= auth
    assert {
        "secrets-ready",
        "postgres-ready",
        "storage-ready",
        "keycloak-realm",
        "opa",
    } <= auth
    assert "extractor" in ingest and "extractor" not in auth


@pytest.mark.parametrize(
    "mutation,error",
    [
        ("missing", "missing dependency"),
        ("cycle", "dependency cycle"),
        ("duplicate", "duplicate Kustomizations"),
    ],
)
def test_graph_validates_even_components_suspended_in_both_legs(graph, mutation, error):
    by_set, _ = copy.deepcopy(graph)
    if mutation == "missing":
        by_set["./flux"]["superset-server"].append("removed-component")
    elif mutation == "cycle":
        by_set["./flux"]["superset-server"].append("superset-dashboards")
    else:
        by_set["./modes/on-prem"]["superset-server"] = []
    with pytest.raises(legs.LegError, match=error):
        legs.dependency_graph(by_set)


def test_duplicate_component_within_source_file_is_rejected(tmp_path):
    doc = {
        "apiVersion": "kustomize.toolkit.fluxcd.io/v1",
        "kind": "Kustomization",
        "metadata": {"name": "same"},
        "spec": {},
    }
    (tmp_path / "duplicate.yaml").write_text(yaml.safe_dump_all([doc, doc]))
    with pytest.raises(legs.LegError, match="duplicate Kustomization"):
        legs.kustomizations(tmp_path)


@pytest.mark.parametrize(
    "mutation,error",
    [
        ("missing", "missing.*keycloak-realm"),
        ("stale", "extra.*removed-component"),
        ("duplicate", "duplicate names"),
    ],
)
def test_auth_wait_list_cannot_silently_drift(graph, tmp_path, mutation, error):
    leg = dict(legs.LEGS["auth"])
    names = leg["wait"].read_text().split()
    if mutation == "missing":
        names.remove("keycloak-realm")
    else:
        names.append("removed-component" if mutation == "stale" else "launchpad")
    leg["wait"] = tmp_path / "wait.txt"
    leg["wait"].write_text("\n".join(names))
    with pytest.raises(legs.LegError, match=error):
        legs.validate_leg("auth", leg, *graph)


@pytest.mark.parametrize(
    "mutation,error",
    [
        ("missing-root", "cover both"),
        ("active-suspended", "expected"),
        ("stale-selector", "stale or wrong-root"),
        ("wrong-set", "stale or wrong-root"),
        ("false-suspend", "explicitly set true"),
        ("wrong-source", "changed source"),
        ("parent-wait", "site ordering"),
    ],
)
def test_auth_root_policy_cannot_drift(graph, tmp_path, mutation, error):
    leg = dict(legs.LEGS["auth"])
    roots = list(yaml.safe_load_all(leg["roots"].read_text()))
    suspend = roots[0]["spec"]["patches"][1]
    if mutation == "missing-root":
        roots.pop()
    elif mutation in ("active-suspended", "stale-selector", "wrong-set"):
        addition = {
            "active-suspended": "launchpad",
            "stale-selector": "deleted",
            "wrong-set": "oauth2-proxy",
        }[mutation]
        suspend["target"]["name"] = suspend["target"]["name"].replace(
            ")$", "|" + addition + ")$"
        )
    elif mutation == "false-suspend":
        suspend["patch"] = suspend["patch"].replace("true", "false")
    elif mutation == "wrong-source":
        roots[0]["spec"]["sourceRef"]["name"] = "another-config"
    else:
        roots[0]["spec"]["wait"] = True
    leg["roots"] = tmp_path / "roots.yaml"
    leg["roots"].write_text(yaml.safe_dump_all(roots))
    with pytest.raises(legs.LegError, match=error):
        legs.validate_leg("auth", leg, *graph)


@pytest.mark.parametrize("leg_name", ["ingest", "auth"])
@pytest.mark.skipif(
    shutil.which("flux") is None,
    reason="Flux CLI required for offline controller render",
)
def test_actual_flux_render_selects_exact_leg(graph, tmp_path, leg_name):
    """Exercise Flux's real patch selector/render behavior without an API server."""
    by_set, deps = graph
    leg = legs.LEGS[leg_name]
    expected = legs.validate_leg(leg_name, leg, by_set, deps)
    source = tmp_path / "artifact"
    shutil.copytree(legs.REPO / "deploy", source)
    rendered = {}
    for index, root in enumerate(yaml.safe_load_all(leg["roots"].read_text())):
        # Dry-run skips substituteFrom API reads; this is the graph's only var.
        root["spec"]["postBuild"]["substitute"] = {"keycloak_namespace": "scout-core"}
        root_file = tmp_path / ("root-" + str(index) + ".yaml")
        root_file.write_text(yaml.safe_dump(root))
        result = subprocess.run(
            [
                "flux",
                "build",
                "kustomization",
                root["metadata"]["name"],
                "--path",
                str(source / root["spec"]["path"]),
                "--kustomization-file",
                str(root_file),
                "--dry-run",
                "--strict-substitute",
            ],
            capture_output=True,
            text=True,
            env={**os.environ, "KUBECONFIG": "/dev/null"},
            timeout=30,
        )
        assert result.returncode == 0, result.stderr
        for doc in yaml.safe_load_all(result.stdout):
            if doc and doc.get("kind") == "Kustomization":
                rendered[doc["metadata"]["name"]] = doc["spec"]
    assert set(rendered) == set(deps)
    assert {
        name for name, spec in rendered.items() if not spec.get("suspend", False)
    } == expected
    assert all(spec["retryInterval"] == "1m" for spec in rendered.values())
    for name in expected:
        assert {d["name"] for d in rendered[name].get("dependsOn", [])} <= expected
