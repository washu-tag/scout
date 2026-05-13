#!/usr/bin/env python3
"""Bootstrap script for Open WebUI declarative config.

Runs as a Job under Helm post-install/post-upgrade hooks (see
helm/open-webui-bootstrap/templates/job.yaml). Replaces what used to be a
chain of Ansible tasks doing kubectl-exec'd curls.

Five phases:
1. Password migration. Rewrite scout-deploy@internal's bcrypt hash to match
   the derived password (no-op on a clean DB; recovers signin on any cluster
   where `open_webui_secret_key` was rotated).
2. Signin or signup. Mint an admin JWT against OWUI's in-cluster Service.
   Falls through to /signup on a truly empty DB (gated by ENABLE_INITIAL_ADMIN_SIGNUP).
3. PersistentConfig re-push. Tool servers (full overwrite), RAG template
   (partial), DEFAULT_MODELS + TASK_MODEL (GET-merge-POST to preserve siblings).
4. Filter functions. Idempotent GET-create-or-update; then set valves and
   toggle global+active to the desired state.
5. Custom models. Idempotent GET-create-or-update one per scout_models[].ui
   entry, plus hide-base overrides on the raw Ollama tags.

Inputs (from env, set by the Job spec):
  OWUI_BASE_URL          — e.g., http://open-webui:8080
  BOOTSTRAP_EMAIL        — primary key for the bootstrap admin user
  BOOTSTRAP_NAME         — display name
  WEBUI_SECRET_KEY       — used to derive the bootstrap password

Inputs (from /app/config/, mounted from a ConfigMap the chart renders):
  persistent_config.json — dict with tool_server_connections / rag_template /
                           default_model_id / task_model_id (any subset)
  filters.json           — list of filter-function specs
  models.json            — list of ModelForm payloads
"""

import hashlib
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

CONFIG_DIR = "/app/config"
HTTP_TIMEOUT = 30
SIGNIN_RETRIES = 20
SIGNIN_RETRY_DELAY_SEC = 3

OWUI_BASE = os.environ["OWUI_BASE_URL"]
BOOTSTRAP_EMAIL = os.environ["BOOTSTRAP_EMAIL"]
BOOTSTRAP_NAME = os.environ["BOOTSTRAP_NAME"]
BOOTSTRAP_PASSWORD = hashlib.sha256(
    os.environ["WEBUI_SECRET_KEY"].encode("utf-8")
).hexdigest()


def http(method, path, body=None, token=None):
    """Call OWUI and return (status_code, response_body_text)."""
    url = f"{OWUI_BASE}{path}"
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(url, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as resp:
            return resp.status, resp.read().decode("utf-8")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8")
    except urllib.error.URLError as e:
        return 0, str(e)


def load_config(filename):
    """Read /app/config/<filename> as JSON, or None if it's missing."""
    path = os.path.join(CONFIG_DIR, filename)
    if not os.path.exists(path):
        return None
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def migrate_password():
    """Set scout-deploy@internal's bcrypt hash via OWUI's own DB session.

    Idempotent: rows that don't exist (clean DB) get a no-op; rows that exist
    get rehashed with a fresh salt. Used by callers who want signin to succeed
    on the next attempt — handles rotated secret keys, prior trusted-header
    bootstraps, or normal re-runs.
    """
    import bcrypt  # noqa: PLC0415  (kept lazy to give a cleaner error if missing)
    from open_webui.internal.db import get_db  # noqa: PLC0415
    from open_webui.models.auths import Auth  # noqa: PLC0415

    hashed = bcrypt.hashpw(BOOTSTRAP_PASSWORD.encode("utf-8"), bcrypt.gensalt()).decode(
        "utf-8"
    )
    with get_db() as db:
        row = db.query(Auth).filter(Auth.email == BOOTSTRAP_EMAIL).first()
        if row is not None:
            row.password = hashed
            db.commit()
            print(f"  migrated existing bcrypt hash for {BOOTSTRAP_EMAIL}")
        else:
            print(f"  no-op (no {BOOTSTRAP_EMAIL} row yet — fresh DB)")


def mint_admin_jwt():
    """Try signin first; fall back to signup on 400 (no such user / fresh DB).

    Retries the initial signin call to absorb pod-not-quite-ready races: OWUI's
    readiness probe can flip Ready before the Python process is bound to :8080.
    """
    signin_body = {"email": BOOTSTRAP_EMAIL, "password": BOOTSTRAP_PASSWORD}
    last_signin = (0, "")

    for attempt in range(SIGNIN_RETRIES):
        code, body = http("POST", "/api/v1/auths/signin", signin_body)
        last_signin = (code, body)
        if code == 200:
            return json.loads(body)["token"], "signin"
        if code == 400:
            # Pydantic / auth-level rejection — won't fix itself on retry.
            break
        print(
            f"  signin attempt {attempt + 1}: HTTP {code}; retrying in {SIGNIN_RETRY_DELAY_SEC}s",
            file=sys.stderr,
        )
        time.sleep(SIGNIN_RETRY_DELAY_SEC)

    code, body = http(
        "POST",
        "/api/v1/auths/signup",
        {
            "name": BOOTSTRAP_NAME,
            "email": BOOTSTRAP_EMAIL,
            "password": BOOTSTRAP_PASSWORD,
        },
    )
    if code == 200:
        return json.loads(body)["token"], "signup"

    raise RuntimeError(
        f"Bootstrap failed.\n"
        f"  signin: HTTP {last_signin[0]} {last_signin[1]}\n"
        f"  signup: HTTP {code} {body}\n"
        f"Diagnostic checklist:\n"
        f"  - Is ENABLE_INITIAL_ADMIN_SIGNUP=true in the OWUI pod env?\n"
        f"  - Did the OWUI pod restart after Helm picked up the env vars?\n"
        f"  - On a non-clean cluster where {BOOTSTRAP_EMAIL} was deleted but\n"
        f"    other users exist, /signup will fail with ACCESS_PROHIBITED —\n"
        f"    re-create the row manually (see the role README)."
    )


def assert_2xx(method, path, code, body):
    if not 200 <= code < 300:
        raise RuntimeError(f"{method} {path} failed: HTTP {code} {body}")


def push_persistent_config(token):
    cfg = load_config("persistent_config.json") or {}

    # Tool servers — full overwrite by design (handler unconditionally assigns).
    if cfg.get("tool_server_connections") is not None:
        code, body = http(
            "POST",
            "/api/v1/configs/tool_servers",
            {"TOOL_SERVER_CONNECTIONS": cfg["tool_server_connections"]},
            token,
        )
        assert_2xx("POST", "/api/v1/configs/tool_servers", code, body)
        print(
            f'  tool_server_connections: pushed {len(cfg["tool_server_connections"])} entries'
        )

    # RAG template — partial works because the handler null-merges siblings.
    if cfg.get("rag_template"):
        code, body = http(
            "POST",
            "/api/v1/retrieval/config/update",
            {"RAG_TEMPLATE": cfg["rag_template"]},
            token,
        )
        assert_2xx("POST", "/api/v1/retrieval/config/update", code, body)
        print(f'  rag_template: pushed ({len(cfg["rag_template"])} chars)')

    # DEFAULT_MODELS — handler unconditionally assigns; GET-merge-POST.
    if cfg.get("default_model_id"):
        code, body = http("GET", "/api/v1/configs/models", token=token)
        assert_2xx("GET", "/api/v1/configs/models", code, body)
        merged = json.loads(body)
        merged["DEFAULT_MODELS"] = cfg["default_model_id"]
        code, body = http("POST", "/api/v1/configs/models", merged, token)
        assert_2xx("POST", "/api/v1/configs/models", code, body)
        print(f'  default_model_id: {cfg["default_model_id"]}')

    # TASK_MODEL — same GET-merge-POST.
    if cfg.get("task_model_id"):
        code, body = http("GET", "/api/v1/tasks/config", token=token)
        assert_2xx("GET", "/api/v1/tasks/config", code, body)
        merged = json.loads(body)
        merged["TASK_MODEL"] = cfg["task_model_id"]
        code, body = http("POST", "/api/v1/tasks/config/update", merged, token)
        assert_2xx("POST", "/api/v1/tasks/config/update", code, body)
        print(f'  task_model_id: {cfg["task_model_id"]}')


def push_filter_functions(token):
    filters = load_config("filters.json") or []
    for fn in filters:
        fn_id = fn["id"]
        # Exists?
        code, _ = http("GET", f"/api/v1/functions/id/{fn_id}", token=token)
        payload = {
            "id": fn_id,
            "name": fn["name"],
            "content": fn["content"],
            "meta": {"description": fn.get("description", "")},
        }
        if code == 200:
            c, b = http("POST", f"/api/v1/functions/id/{fn_id}/update", payload, token)
            assert_2xx("POST", f"/api/v1/functions/id/{fn_id}/update", c, b)
            print(f"  {fn_id}: updated")
        else:
            c, b = http("POST", "/api/v1/functions/create", payload, token)
            assert_2xx("POST", "/api/v1/functions/create", c, b)
            print(f"  {fn_id}: created")

        # Valves
        if fn.get("valves"):
            c, b = http(
                "POST",
                f"/api/v1/functions/id/{fn_id}/valves/update",
                fn["valves"],
                token,
            )
            assert_2xx("POST", f"/api/v1/functions/id/{fn_id}/valves/update", c, b)

        # Toggles — GET current, only flip when state differs.
        c, b = http("GET", f"/api/v1/functions/id/{fn_id}", token=token)
        assert_2xx("GET", f"/api/v1/functions/id/{fn_id}", c, b)
        current = json.loads(b)

        desired_global = fn.get("enable_global", False)
        if current.get("is_global", False) != desired_global:
            c, b = http(
                "POST", f"/api/v1/functions/id/{fn_id}/toggle/global", token=token
            )
            assert_2xx("POST", f"/api/v1/functions/id/{fn_id}/toggle/global", c, b)
            print(f"  {fn_id}: is_global → {desired_global}")

        desired_active = fn.get("enable_active", True)
        if current.get("is_active", False) != desired_active:
            c, b = http("POST", f"/api/v1/functions/id/{fn_id}/toggle", token=token)
            assert_2xx("POST", f"/api/v1/functions/id/{fn_id}/toggle", c, b)
            print(f"  {fn_id}: is_active → {desired_active}")


def push_models(token):
    models = load_config("models.json") or []
    for m in models:
        if not m.get("id"):
            raise RuntimeError(f"model payload missing id: {m}")
        model_id_q = urllib.parse.quote(m["id"])
        code, _ = http("GET", f"/api/v1/models/model?id={model_id_q}", token=token)
        if code == 200:
            c, b = http(
                "POST", f"/api/v1/models/model/update?id={model_id_q}", m, token
            )
            assert_2xx("POST", f'/api/v1/models/model/update?id={m["id"]}', c, b)
            print(f'  {m["id"]}: updated')
        else:
            c, b = http("POST", "/api/v1/models/create", m, token)
            assert_2xx("POST", "/api/v1/models/create", c, b)
            print(f'  {m["id"]}: created')


def main():
    print(f"Bootstrap against {OWUI_BASE} as {BOOTSTRAP_EMAIL}")

    print("[1/5] Migrating bootstrap user password (idempotent)...")
    migrate_password()

    print("[2/5] Minting admin JWT...")
    token, via = mint_admin_jwt()
    print(f"  JWT acquired via {via}")

    print("[3/5] Pushing PersistentConfig...")
    push_persistent_config(token)

    print("[4/5] Configuring filter functions...")
    push_filter_functions(token)

    print("[5/5] Configuring custom models...")
    push_models(token)

    print("Bootstrap complete.")


if __name__ == "__main__":
    main()
