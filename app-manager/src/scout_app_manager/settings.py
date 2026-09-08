"""Process configuration, read from the environment as each Settings is built."""

from typing import Literal

from pydantic import AliasChoices, Field, ValidationError
from pydantic_settings import BaseSettings, SettingsConfigDict

ENV_PREFIX = "APP_MANAGER_"

# What the manager may do with a composed realm. `diff` reports it, `apply`
# writes it. Closed because this one input decides whether the component does
# anything at all.
ApplyMode = Literal["diff", "apply"]


class Settings(BaseSettings):
    # Ignored, not forbidden: the chart also sets APP_MANAGER_LOG_LEVEL, which
    # main.py reads straight from the environment.
    model_config = SettingsConfigDict(env_prefix=ENV_PREFIX, extra="ignore")

    fragment_dir: str = "/fragments"
    # The only field whose variable is not its name: the second choice is what
    # keeps keyword construction working.
    base_realm_path: str = Field(
        "/base-realm/scout-realm.json",
        validation_alias=AliasChoices("APP_MANAGER_BASE_REALM", "base_realm_path"),
    )
    # Where the service reads that same document from instead: by name, from
    # the API, every reconcile. A mounted copy is refreshed lazily and would
    # still hold the previous bytes when a notification arrives. Empty leaves
    # base_realm_path as the only source, which is how the CLI reads it.
    base_realm_configmap: str = ""
    base_realm_key: str = "scout-realm.json"
    domain: str = "scout.example.edu"
    namespace: str = ""
    # Not a Secret: the composed realm names its credentials, never carries
    # them.
    composed_configmap: str = "keycloak-config-composed"
    status_configmap: str = "scout-app-manager-status"
    # Fail-safe, and deliberately not the deployment default: a process nobody
    # configured must not write a realm. Both charts set this to `apply`,
    # because there the reconciler is the realm's only writer (ADR 0037).
    apply_mode: ApplyMode = "diff"
    keycloak_url: str = "http://keycloak-service:8080"
    keycloak_realm: str = "scout"
    signout_url: str = ""
    admin_secret: str = "keycloak-admin-secret"
    # Keys are the base realm's `$(env:...)` variables verbatim; the apply Job
    # takes it wholesale with envFrom.
    client_secrets_secret: str = "keycloak-client-secrets"
    config_cli_image: str = "docker.io/adorsys/keycloak-config-cli:6.5.1-26.5.5"
    # A floor, not a schedule: a change to a watched object is the normal
    # wake-up. It is also the worst case for the one input nothing can watch --
    # a realm written by something other than this reconciler, which is a
    # Keycloak read rather than a Kubernetes event.
    resync_seconds: int = 60
    # One request per resource written, so a startup sync arrives as a burst.
    debounce_seconds: float = 2.0
    discovery_health_url: str = "http://127.0.0.1:8081/healthz"
    discovery_wait_seconds: int = 90
    # Watch this namespace's Secrets and its base realm ConfigMap, and wake on
    # a change. Off falls back to the resync floor, which is the break-glass if
    # the watch ever misbehaves.
    object_watch: bool = True
    # Absence tolerated before a fragment's realm objects are retracted. Longer
    # than a chart upgrade's delete-then-create window.
    retraction_grace_seconds: int = 300
    job_timeout_seconds: int = 300
    job_ttl_seconds: int = 3600
    port: int = 8080
    # Loopback only. This port can trigger a realm write.
    reload_port: int = 8082

    def __init__(self, **values: object) -> None:
        try:
            super().__init__(**values)
        except ValidationError as exc:
            # A misconfigured pod should say which variable is wrong and stop,
            # not print a pydantic traceback.
            raise SystemExit("; ".join(_problems(exc))) from None


def _problems(exc: ValidationError) -> list[str]:
    """Name the environment variable an operator set, not the field behind it."""
    out = []
    for error in exc.errors():
        field = str(error["loc"][0]) if error["loc"] else "(unknown)"
        name = field if field.startswith(ENV_PREFIX) else ENV_PREFIX + field.upper()
        out.append(f"{name}: {error['msg']}, not {error['input']!r}")
    return out
