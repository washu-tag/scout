from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Service-wide config, populated from the environment.

    All defaults are dev-friendly; production values come from the Helm
    chart's env block (rendered by `ansible/roles/datasets_service`).
    """

    model_config = SettingsConfigDict(env_prefix="DATASETS_", case_sensitive=False)

    # HTTP
    host: str = "0.0.0.0"
    port: int = 8000
    log_level: str = "info"

    # Postgres
    database_url: str = "postgresql://datasets:datasets@localhost:5432/datasets"

    # Trino (read-only analytics instance — user JWT pass-through)
    trino_host: str = "trino"
    trino_port: int = 8080
    trino_scheme: str = "http"
    trino_catalog: str = "delta"
    trino_schema: str = "default"
    trino_ca_cert: str | None = None

    # No trino-rw / write-side config — the service never writes to
    # Trino. Cohorts are saved SQL in Postgres; rows are evaluated on
    # demand against the read-only Trino instance.

    # Auth — Phase 0 uses a shared header secret. Phase 2 replaces this
    # with Keycloak JWT validation; the dev_shared_secret stays available
    # as an opt-in escape hatch for in-cluster smoke tests.
    dev_shared_secret: str = ""
    keycloak_jwks_url: str = ""
    keycloak_audience: str = "datasets-service"
    # Expected `iss` claim — Keycloak realm URL. Empty disables iss check
    # (useful for tests). The Ansible role sets this to
    # `{keycloak_realm_url}` so it matches what voila / OWUI mint.
    keycloak_issuer: str = ""

    # Dataset behavior
    dataset_ttl_days: int = 30

    # CSP — comma-separated list of origins that are allowed to embed the
    # viewer in an iframe. The default lets the Scout chat ingress embed
    # us; tighten per env via env var. Empty disables the CSP header.
    csp_frame_ancestors: str = "https://chat.dev02.tag.rcif.io"

    # OWUI admin credentials — used by the /webhooks/owui-new-user
    # receiver to flip newly-signed-up users from pending → user (the
    # OWUI admin webhook fires on signup; this receiver consumes that
    # event and calls OWUI's user-update API). Empty disables the
    # webhook handler (it 503s).
    owui_base_url: str = "http://open-webui.scout-analytics:80"
    owui_admin_email: str = "scout-deploy@scout-deploy.local"
    owui_admin_password: str = ""
    # Shared secret OWUI sends in the webhook header (or query string)
    # so we don't auto-enable a forged signup. Empty = no verification
    # (acceptable in dev when NetworkPolicy already gates ingress).
    owui_webhook_secret: str = ""


settings = Settings()
