#!/usr/bin/env python3
"""Bootstrap script for Open WebUI declarative config.

Runs as a Job under Helm post-install/post-upgrade hooks (see
helm/open-webui-bootstrap/templates/job.yaml). Replaces what used to be a
chain of Ansible tasks doing kubectl-exec'd curls.

Six phases:
1. Password migration. Rewrite the bootstrap user's bcrypt hash to match
   the configured password (no-op on a clean DB; recovers signin on any
   cluster where `open_webui_bootstrap_password` was rotated).
2. Signin or signup. Mint an admin JWT against OWUI's in-cluster Service.
   Falls through to /signup on a truly empty DB (gated by ENABLE_INITIAL_ADMIN_SIGNUP).
3. PersistentConfig re-push. Tool servers (full overwrite); DEFAULT_MODELS
   + TASK_MODEL (GET-merge-POST to preserve siblings). RAG template is not
   pushed - Scout Explorer models use function_calling: native, which
   bypasses OWUI's RAG auto-injection path entirely.
4. Filter functions. Idempotent GET-create-or-update; then set valves and
   toggle global+active to the desired state.
5. Python tools. Same idempotent GET-create-or-update pattern as filters,
   against /api/v1/tools. Each entry's `content_file` is inlined as the
   tool source. Public read access grant so end users can attach the tool.
6. Custom models. Idempotent GET-create-or-update one per scout_models[].ui
   entry, plus hide-base overrides on the raw Ollama tags.

Inputs (from env, set by the Job spec):
  OWUI_BASE_URL          - e.g., http://open-webui:80
  BOOTSTRAP_EMAIL        - primary key for the bootstrap admin user
  BOOTSTRAP_NAME         - display name
  BOOTSTRAP_PASSWORD     - bootstrap admin password (from a Secret)

Inputs (from /app/config/, mounted from a ConfigMap the chart renders):
  persistent_config.json - dict with tool_server_connections / default_model_id
                           / task_model_id (any subset)
  filters.json           - list of filter-function specs; each entry's
                           `content_file` names a sibling file in /app/config/
                           whose contents are inlined as the filter `content`.
  tools.json             - list of Python-tool specs; same `content_file`
                           inline pattern as filters.json.
  models.json            - list of ModelForm payloads; each entry's
                           `params.system_file` names a sibling file whose
                           contents are inlined as `params.system`.
"""

import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

import bcrypt

CONFIG_DIR = "/app/config"
HTTP_TIMEOUT = 30
SIGNIN_RETRIES = 20
SIGNIN_RETRY_DELAY_SEC = 3

OWUI_BASE = os.environ["OWUI_BASE_URL"]
BOOTSTRAP_EMAIL = os.environ["BOOTSTRAP_EMAIL"]
BOOTSTRAP_NAME = os.environ["BOOTSTRAP_NAME"]
BOOTSTRAP_PASSWORD = os.environ["BOOTSTRAP_PASSWORD"]


def http(method, path, body=None, token=None):
    """Call OWUI and return (status_code, response_body_text)."""
    url = f"{OWUI_BASE}{path}"
    # urllib.urlopen accepts file://, ftp://, etc. by default; allowlist HTTP(S) only.
    if not url.startswith(("http://", "https://")):
        raise ValueError(f"Refusing to call non-HTTP(S) URL: {url}")
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(url, data=data, method=method, headers=headers)
    try:
        # nosemgrep: python.lang.security.audit.dynamic-urllib-use-detected.dynamic-urllib-use-detected
        with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as resp:  # noqa: S310
            return resp.status, resp.read().decode("utf-8")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8")
    except urllib.error.URLError as e:
        return 0, str(e)


def http_or_raise(method, path, body=None, token=None):
    """http() that raises on non-2xx. Returns response body text."""
    code, response = http(method, path, body=body, token=token)
    if not 200 <= code < 300:
        raise RuntimeError(f"{method} {path} failed: HTTP {code} {response}")
    return response


def get_merge_post(get_path, post_path, key, value, token):
    """OWUI's models/tasks config handlers unconditionally assign every form
    field - partial POSTs would clobber siblings. Read current, set one key,
    write back."""
    current = json.loads(http_or_raise("GET", get_path, token=token))
    current[key] = value
    http_or_raise("POST", post_path, current, token)


def load_config(filename):
    """Read /app/config/<filename> as JSON, or None if it's missing."""
    path = os.path.join(CONFIG_DIR, filename)
    if not os.path.exists(path):
        return None
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def load_text(filename):
    """Read /app/config/<filename> as raw text.

    Used to resolve filter `content_file` and model `params.system_file`
    references in the JSON payloads - bootstrap.py inlines these as `content`
    / `params.system` before POSTing to OWUI. Missing file is fatal: the
    payload references it explicitly, so silent skip would mask a bug.
    """
    path = os.path.join(CONFIG_DIR, filename)
    with open(path, encoding="utf-8") as f:
        return f.read()


def migrate_password():
    """Set the bootstrap user's bcrypt hash via OWUI's own DB session.

    Idempotent: rows that don't exist (clean DB) get a no-op; rows that exist
    get rehashed with a fresh salt. Lets signin succeed on the next attempt
    after a bootstrap-password rotation.
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
            print(f"  no-op (no {BOOTSTRAP_EMAIL} row yet - fresh DB)")


def mint_admin_jwt():
    """Try signin first; fall back to signup on 400 (no such user / fresh DB).

    Retries the initial signin call to absorb pod-not-quite-ready races: OWUI's
    readiness probe can flip Ready before the Python process is bound to :8080.
    """
    signin_payload = {"email": BOOTSTRAP_EMAIL, "password": BOOTSTRAP_PASSWORD}
    last_code, last_body = 0, ""

    for attempt in range(SIGNIN_RETRIES):
        last_code, last_body = http("POST", "/api/v1/auths/signin", signin_payload)
        if last_code == 200:
            return json.loads(last_body)["token"], "signin"
        if last_code == 400:
            # Pydantic / auth-level rejection - won't fix itself on retry.
            break
        print(
            f"  signin attempt {attempt + 1}: HTTP {last_code}; retrying in {SIGNIN_RETRY_DELAY_SEC}s",
            file=sys.stderr,
        )
        time.sleep(SIGNIN_RETRY_DELAY_SEC)

    signup_payload = {
        "name": BOOTSTRAP_NAME,
        "email": BOOTSTRAP_EMAIL,
        "password": BOOTSTRAP_PASSWORD,
    }
    signup_code, signup_body = http("POST", "/api/v1/auths/signup", signup_payload)
    if signup_code == 200:
        return json.loads(signup_body)["token"], "signup"

    raise RuntimeError(
        f"Bootstrap failed.\n"
        f"  signin: HTTP {last_code} {last_body}\n"
        f"  signup: HTTP {signup_code} {signup_body}\n"
        f"Diagnostic checklist:\n"
        f"  - Is ENABLE_INITIAL_ADMIN_SIGNUP=true in the OWUI pod env?\n"
        f"  - Did the OWUI pod restart after Helm picked up the env vars?\n"
        f"  - On a non-clean cluster where {BOOTSTRAP_EMAIL} was deleted but\n"
        f"    other users exist, /signup will fail with ACCESS_PROHIBITED -\n"
        f"    re-create the row manually (see the role README)."
    )


def push_persistent_config(token):
    cfg = load_config("persistent_config.json") or {}

    # Tool servers - full overwrite by design (handler unconditionally assigns).
    if cfg.get("tool_server_connections") is not None:
        http_or_raise(
            "POST",
            "/api/v1/configs/tool_servers",
            {"TOOL_SERVER_CONNECTIONS": cfg["tool_server_connections"]},
            token,
        )
        print(
            f'  tool_server_connections: pushed {len(cfg["tool_server_connections"])} entries'
        )

    if cfg.get("default_model_id"):
        get_merge_post(
            "/api/v1/configs/models",
            "/api/v1/configs/models",
            "DEFAULT_MODELS",
            cfg["default_model_id"],
            token,
        )
        print(f'  default_model_id: {cfg["default_model_id"]}')

    if cfg.get("task_model_id"):
        get_merge_post(
            "/api/v1/tasks/config",
            "/api/v1/tasks/config/update",
            "TASK_MODEL",
            cfg["task_model_id"],
            token,
        )
        print(f'  task_model_id: {cfg["task_model_id"]}')

    if cfg.get("webhook_url") is not None:
        # Auto-enable receiver for new-user signups.
        http_or_raise(
            "POST",
            "/api/webhook",
            {"url": cfg["webhook_url"]},
            token,
        )
        print(f'  webhook_url: {cfg["webhook_url"] or "(cleared)"}')


def push_filter_functions(token):
    for fn in load_config("filters.json") or []:
        fn_id = fn["id"]
        fn_id_q = urllib.parse.quote(fn_id, safe="")
        payload = {
            "id": fn_id,
            "name": fn["name"],
            "content": load_text(fn["content_file"]),
            "meta": {"description": fn.get("description", "")},
        }

        exists_code, _ = http("GET", f"/api/v1/functions/id/{fn_id_q}", token=token)
        if exists_code == 200:
            http_or_raise(
                "POST", f"/api/v1/functions/id/{fn_id_q}/update", payload, token
            )
            print(f"  {fn_id}: updated")
        else:
            http_or_raise("POST", "/api/v1/functions/create", payload, token)
            print(f"  {fn_id}: created")

        if fn.get("valves"):
            http_or_raise(
                "POST",
                f"/api/v1/functions/id/{fn_id_q}/valves/update",
                fn["valves"],
                token,
            )

        current = json.loads(
            http_or_raise("GET", f"/api/v1/functions/id/{fn_id_q}", token=token)
        )

        desired_global = fn.get("enable_global", False)
        if current.get("is_global", False) != desired_global:
            http_or_raise(
                "POST", f"/api/v1/functions/id/{fn_id_q}/toggle/global", token=token
            )
            print(f"  {fn_id}: is_global → {desired_global}")

        desired_active = fn.get("enable_active", True)
        if current.get("is_active", False) != desired_active:
            http_or_raise("POST", f"/api/v1/functions/id/{fn_id_q}/toggle", token=token)
            print(f"  {fn_id}: is_active → {desired_active}")


def push_tools(token):
    """Upsert Python tools against /api/v1/tools. Tools attach per-model
    via toolIds (no global/active toggles), so we push source, meta,
    valves, and re-assert public-read access grants."""
    public_read = [
        {"principal_type": "user", "principal_id": "*", "permission": "read"}
    ]
    for t in load_config("tools.json") or []:
        tool_id = t["id"]
        tool_id_q = urllib.parse.quote(tool_id, safe="")
        payload = {
            "id": tool_id,
            "name": t["name"],
            "content": load_text(t["content_file"]),
            "meta": {
                "description": t.get("description", ""),
                "manifest": t.get("manifest", {}),
            },
            "access_grants": public_read,
        }
        exists_code, _ = http("GET", f"/api/v1/tools/id/{tool_id_q}", token=token)
        if exists_code == 200:
            http_or_raise(
                "POST", f"/api/v1/tools/id/{tool_id_q}/update", payload, token
            )
            print(f"  {tool_id}: updated")
        else:
            http_or_raise("POST", "/api/v1/tools/create", payload, token)
            print(f"  {tool_id}: created")

        if t.get("valves"):
            http_or_raise(
                "POST",
                f"/api/v1/tools/id/{tool_id_q}/valves/update",
                t["valves"],
                token,
            )

        # Re-assert public-read access grants on every run via the dedicated
        # endpoint. The update payload's `access_grants` is only honored on
        # create, so this ensures the grant stays correct even if someone
        # edited it in the UI.
        http_or_raise(
            "POST",
            f"/api/v1/tools/id/{tool_id_q}/access/update",
            {"access_grants": public_read},
            token,
        )


def push_models(token):
    for m in load_config("models.json") or []:
        if not m.get("id"):
            raise RuntimeError(f"model payload missing id: {m}")
        # Inline the system prompt from its referenced file. Keeps the JSON
        # payload small and avoids duplicating ~24 KB of prompt across every
        # Scout Explorer entry. Hide-base entries leave `params` empty, so
        # there's nothing to inline for them.
        system_file = m.get("params", {}).pop("system_file", None)
        if system_file:
            m["params"]["system"] = load_text(system_file)

        model_id_q = urllib.parse.quote(m["id"], safe="")
        exists_code, _ = http(
            "GET", f"/api/v1/models/model?id={model_id_q}", token=token
        )
        if exists_code == 200:
            http_or_raise(
                "POST", f"/api/v1/models/model/update?id={model_id_q}", m, token
            )
            print(f'  {m["id"]}: updated')
        else:
            http_or_raise("POST", "/api/v1/models/create", m, token)
            print(f'  {m["id"]}: created')


def main():
    print(f"Bootstrap against {OWUI_BASE} as {BOOTSTRAP_EMAIL}")

    print("[1/6] Migrating bootstrap user password (idempotent)...")
    migrate_password()

    print("[2/6] Minting admin JWT...")
    token, via = mint_admin_jwt()
    print(f"  JWT acquired via {via}")

    print("[3/6] Pushing PersistentConfig...")
    push_persistent_config(token)

    print("[4/6] Configuring filter functions...")
    push_filter_functions(token)

    print("[5/6] Configuring Python tools...")
    push_tools(token)

    print("[6/6] Configuring custom models...")
    push_models(token)

    print("Bootstrap complete.")


if __name__ == "__main__":
    main()
