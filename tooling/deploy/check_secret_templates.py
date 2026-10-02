#!/usr/bin/env python3
"""CI check for the on-prem secret contract (validate-deploy.yaml): renders
``deploy/base/secrets-on-prem`` through ``flux build kustomization --dry-run`` (the
controller's own re-serialize-then-substitute code, offline) and checks the templates
against their consumers. Needs flux and kustomize on PATH; never touches a cluster.
"""

from __future__ import annotations

import collections
import functools
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import yaml

from gen_cluster_vars import build as build_cluster_vars
from gen_cluster_vars import load_required

HERE = Path(__file__).resolve().parent
DEPLOY = HERE.parents[1] / "deploy"
TEMPLATES = DEPLOY / "base" / "secrets-on-prem"
GATE = DEPLOY / "modes" / "on-prem" / "secrets.yaml"
# $$ is Flux's escape for a literal $; ${name} and ${name:=default} are substituted.
VAR = re.compile(r"\$\$|\$\{([_A-Za-z][_A-Za-z0-9]*)(:=([^}]*))?\}")
SQ = "${sq}"
# A base names a Secret under a key ending like these: {name, key?} refs, strings
# (with the key in a secretKey/existingSecretKey sibling), or lists of {name}.
SECRET_KEY_SUFFIXES = ("secret", "secretname", "secretref", "secretkeyref", "secrets")
SIBLING_KEYS = ("secretKey", "existingSecretKey")


def flux_files(mode: str) -> list:
    """The shared Flux files plus one mode set's, without the kustomization.yaml."""
    files = sorted((DEPLOY / "flux").glob("*.yaml"))
    files += sorted((DEPLOY / "modes" / mode).glob("*.yaml"))
    return [f for f in files if f.name != "kustomization.yaml"]


def kustomizations(mode: str) -> list:
    """(path relative to deploy/, doc) of each Flux Kustomization in that set that
    renders the scout-config artifact."""
    out = []
    for f in flux_files(mode):
        for d in yaml.safe_load_all(f.read_text()):
            spec = (d or {}).get("spec", {})
            if (
                d
                and d["kind"] == "Kustomization"
                and spec["sourceRef"]["name"] == "scout-config"
            ):
                out.append((os.path.normpath(spec["path"].lstrip("/")), d))
    return out


@functools.lru_cache(maxsize=None)
def kustomize_text(path: str) -> str:
    return subprocess.run(
        ["kustomize", "build", str(DEPLOY / path)],
        capture_output=True,
        text=True,
        check=True,
    ).stdout


def kustomize_build(path: str) -> list:
    return [d for d in yaml.safe_load_all(kustomize_text(path)) if d]


@functools.lru_cache(maxsize=None)
def templates() -> tuple:
    docs = []
    for f in sorted(TEMPLATES.glob("*.yaml")):
        if f.name != "kustomization.yaml":
            docs.extend(d for d in yaml.safe_load_all(f.read_text()) if d)
    return tuple(docs)


def var_names(text: str) -> set:
    return {m[0] for m in VAR.findall(text) if m[0]}


def subst(text: str, env: dict) -> str:
    """Flux's semantics: ${v:=d} takes d when v is unset or empty."""

    def rep(m):
        if m.group(0) == "$$":
            return "$"
        name, has_default, default = m.group(1), m.group(2), m.group(3)
        if name in env and (env[name] != "" or has_default is None):
            return env[name]
        if has_default is not None:
            return default
        raise KeyError(name)

    return VAR.sub(rep, text)


def intended(template_value: str, env: dict) -> str:
    """What a template value should render to: ${sq}...${sq} is a quote, nothing more."""
    if template_value.startswith(SQ) and template_value.endswith(SQ):
        template_value = template_value[len(SQ) : -len(SQ)]
    return subst(template_value, env)


def flux_render(env: dict):
    """(objects, error) of ConfigMap twins of the templates (flux build masks Secret
    values when it prints them), built and substituted by flux."""
    with tempfile.TemporaryDirectory() as tmp:
        tpl = Path(tmp, "tpl")
        shutil.copytree(TEMPLATES, tpl)
        for f in tpl.glob("*.yaml"):
            if f.name == "kustomization.yaml":
                continue
            text = f.read_text().replace("kind: Secret", "kind: ConfigMap")
            text = re.sub(r"(?m)^type: .*\n", "", text.replace("stringData:", "data:"))
            f.write_text(text)
        gate = yaml.safe_load(GATE.read_text())
        gate["spec"]["postBuild"] = {"substitute": env}
        Path(tmp, "ks.json").write_text(json.dumps(gate))
        p = subprocess.run(
            [
                "flux",
                "build",
                "kustomization",
                gate["metadata"]["name"],
                "--path",
                "./tpl",
                "--kustomization-file",
                "./ks.json",
                "--dry-run",
                "--strict-substitute",
            ],
            cwd=tmp,
            capture_output=True,
            text=True,
            env=dict(os.environ, KUBECONFIG=os.devnull),
        )
        if p.returncode:
            return None, p.stderr.strip()
        return [d for d in yaml.safe_load_all(p.stdout) if d], None


def check_render(env: dict, label: str) -> list:
    """Every key renders to exactly its intended string."""
    objs, err = flux_render(env)
    if err:
        return ["[{}] flux build failed: {}".format(label, err)]
    got = {
        (o["metadata"]["namespace"], o["metadata"]["name"]): o.get("data") or {}
        for o in objs
    }
    problems, n = [], 0
    for d in templates():
        ident = (
            subst(d["metadata"]["namespace"], env),
            subst(d["metadata"]["name"], env),
        )
        data = got.pop(ident, None)
        if data is None:
            problems.append("[{}] {}/{} not rendered".format(label, *ident))
            continue
        for key, tmpl in d["stringData"].items():
            n += 1
            want = intended(tmpl, env)
            if data.get(key) != want:
                problems.append(
                    "[{}] {}/{}:{} renders {!r}, want {!r}".format(
                        label, ident[0], ident[1], key, data.get(key), want
                    )
                )
        problems += [
            "[{}] {}/{}:{} rendered but not templated".format(label, *ident, k)
            for k in sorted(set(data) - set(d["stringData"]))
        ]
    problems += ["[{}] {}/{} rendered but not templated".format(label, *i) for i in got]
    if not problems:
        print(
            "  ok   [{}] {} keys in {} Secrets render exactly".format(
                label, n, len(templates())
            )
        )
    return problems


def hostile(env: dict) -> dict:
    """Swap every value a template substitutes for one that breaks unless wrapped.

    Namespaces keep their real values; vars inside Secret names get octal-looking
    ones (0123 parses as an int); the rest get '0NNN #name: v', which an unwrapped
    placeholder truncates at the comment.
    """
    docs = templates()
    ns_vars = {v for d in docs for v in var_names(d["metadata"]["namespace"])}
    name_vars = {v for d in docs for v in var_names(d["metadata"]["name"])}
    used = {v for d in docs for s in d["stringData"].values() for v in var_names(s)}
    out = dict(env)
    for i, name in enumerate(sorted(used - ns_vars - {"sq"})):
        out[name] = (
            "0" + oct(8 + i)[2:]
            if name in name_vars
            else "0{:03d} #{}: v".format(i, name)
        )
    return out


def check_strict(env: dict) -> list:
    """Strict substitution rejects a missing secret value or cluster-var by name."""
    problems = []
    for name in ("postgres_password", "db_port"):
        _, err = flux_render({k: v for k, v in env.items() if k != name})
        if not err or '"{}"'.format(name) not in err:
            problems.append(
                "strict substitution did not reject a missing {}".format(name)
            )
        else:
            print("  ok   strict substitution rejects a missing {}".format(name))
    return problems


def check_metadata() -> list:
    """prune: disabled everywhere, cnpg.io/reload on the CNPG Secrets, no bare $."""
    problems = []
    for d in kustomize_build("base/secrets-on-prem"):
        md = d["metadata"]
        where = "{}/{}".format(md["namespace"], md["name"])
        if d["kind"] != "Secret":
            problems.append("{}: is a {}, not a Secret".format(where, d["kind"]))
        if (md.get("annotations") or {}).get(
            "kustomize.toolkit.fluxcd.io/prune"
        ) != "disabled":
            problems.append(
                "{}: missing kustomize.toolkit.fluxcd.io/prune: disabled".format(where)
            )
        cnpg = md["namespace"] == "${postgres_cluster_namespace}"
        if cnpg and (md.get("labels") or {}).get("cnpg.io/reload") != "true":
            problems.append(
                '{}: CNPG Secret without cnpg.io/reload: "true"'.format(where)
            )
        fields = [("name", md["name"]), ("namespace", md["namespace"])]
        for key, value in fields + list(d["stringData"].items()):
            if "$" in VAR.sub("", value):
                problems.append(
                    "{}:{}: bare $ (Flux substitutes only ${{...}})".format(where, key)
                )
    if not problems:
        print(
            "  ok   templates carry prune: disabled (CNPG: cnpg.io/reload); no bare $"
        )
    return problems


def check_coverage(cluster_vars: dict) -> list:
    """Every (namespace, name[, key]) a base on the on-prem Flux paths references is
    provided, and every template has a consumer."""
    refs = collections.defaultdict(set)  # (ns, name) -> {(key or None, where)}
    provided, configmaps, helmreleases = set(), {}, []

    def ns_of(d):
        ns = d["metadata"].get("namespace", "")
        if d["kind"] == "HelmRelease":
            ns = d["spec"].get("targetNamespace") or ns
        return subst(ns, cluster_vars)

    def ref(ns, name, key, where):
        refs[(ns, subst(name, cluster_vars))].add((key, where))

    def walk(node, ns, where):
        if isinstance(node, list):
            for x in node:
                walk(x, ns, where)
        if not isinstance(node, dict):
            return
        for k, v in node.items():
            if k.lower().endswith(SECRET_KEY_SUFFIXES):
                if (
                    isinstance(v, dict)
                    and isinstance(v.get("name"), str)
                    and not v.get("optional")
                ):
                    ref(ns, v["name"], v.get("key"), where)
                elif isinstance(v, str) and v:
                    key = next(
                        (node[s] for s in SIBLING_KEYS if isinstance(node.get(s), str)),
                        None,
                    )
                    ref(ns, v, key, where)
                elif isinstance(v, list):
                    for item in v:
                        if isinstance(item, dict) and isinstance(item.get("name"), str):
                            ref(ns, item["name"], None, where)
            walk(v, ns, where)

    on_prem = sorted(
        {path for path, _ in kustomizations("on-prem")} - {"base/secrets-on-prem"}
    )
    for path in on_prem:
        for d in kustomize_build(path):
            ns, name = ns_of(d), subst(d["metadata"]["name"], cluster_vars)
            where = "{} {}/{}".format(path, d["kind"], name)
            if d["kind"] == "Secret":
                provided.add((ns, name))
            elif d["kind"] == "ConfigMap":
                configmaps[(ns, name)] = d.get("data") or {}
            else:
                if d["kind"] == "Certificate":
                    provided.add((ns, subst(d["spec"]["secretName"], cluster_vars)))
                if d["kind"] == "HelmRelease":
                    helmreleases.append((ns, d, where))
                walk(d, ns, where)
    # Helm values a HelmRelease reads through valuesFrom (the per-mode edge ConfigMaps).
    for ns, hr, where in helmreleases:
        for src in hr["spec"].get("valuesFrom", []):
            data = configmaps.get((ns, subst(src["name"], cluster_vars)))
            if src["kind"] == "ConfigMap" and data and not src.get("targetPath"):
                walk(
                    yaml.safe_load(data[src.get("valuesKey", "values.yaml")]), ns, where
                )

    templated = {
        (
            subst(d["metadata"]["namespace"], cluster_vars),
            subst(d["metadata"]["name"], cluster_vars),
        ): set(d["stringData"])
        for d in templates()
    }
    problems = []
    for ident, uses in sorted(refs.items()):
        if ident in templated:
            problems += [
                "{}/{} has no key {} ({})".format(*ident, key, where)
                for key, where in sorted(uses, key=str)
                if key and key not in templated[ident]
            ]
        elif ident not in provided:
            where = min(w for _, w in uses)
            problems.append(
                "{}/{} is referenced on-prem but nothing provides it ({})".format(
                    *ident, where
                )
            )
    problems += [
        "template {}/{} has no on-prem consumer".format(*i)
        for i in sorted(set(templated) - set(refs))
    ]
    if not problems:
        print(
            "  ok   all {} templates have a consumer; every on-prem Secret ref is provided".format(
                len(templated)
            )
        )
    return problems


def check_cnpg_roles(env: dict) -> list:
    """CNPG rejects a role Secret whose username differs from the role name, and that
    aborts reconciliation of every managed role; a role without one gets no password."""
    by_name = {
        d["metadata"]["name"]: d
        for d in templates()
        if d["metadata"]["namespace"] == "${postgres_cluster_namespace}"
    }
    problems = []
    for cluster in (
        d for d in kustomize_build("base/postgres/cluster") if d["kind"] == "Cluster"
    ):
        want = [(cluster["spec"]["superuserSecret"]["name"], "postgres")]
        for role in cluster["spec"]["managed"]["roles"]:
            secret = (role.get("passwordSecret") or {}).get("name")
            if not secret:
                problems.append(
                    "CNPG role {} has no passwordSecret".format(role["name"])
                )
                continue
            want.append((secret, subst(role["name"], env)))
        for secret, user in want:
            tpl = by_name.get(secret)
            got = intended(tpl["stringData"]["username"], env) if tpl else None
            if got != user:
                problems.append(
                    "CNPG Secret {} has username {!r}, want {!r}".format(
                        secret, got, user
                    )
                )
    if not problems:
        print("  ok   every CNPG role Secret carries its role's name")
    return problems


def main() -> None:
    fixtures = HERE / "fixtures"
    cv_values = json.loads((fixtures / "cluster-vars.values.json").read_text())
    secret_values = json.loads((fixtures / "secret-values.values.json").read_text())
    cluster_vars = build_cluster_vars(
        cv_values, load_required(DEPLOY / "required-vars.txt")
    )
    inline = yaml.safe_load(GATE.read_text())["spec"]["postBuild"]["substitute"]
    env = {**cluster_vars, **secret_values, **inline}
    problems = check_render(env, "fixture")
    problems += check_render(hostile(env), "hostile")
    problems += check_strict(env)
    problems += check_metadata()
    problems += check_coverage(cluster_vars)
    problems += check_cnpg_roles(env)
    for p in problems:
        print("::error::" + p)
    sys.exit(1 if problems else 0)


if __name__ == "__main__":
    main()
