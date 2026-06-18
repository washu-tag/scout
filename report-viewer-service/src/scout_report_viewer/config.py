from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Service-wide config, populated from the environment.

    All defaults are dev-friendly; production values come from the Helm
    chart's env block (rendered by `ansible/roles/report_viewer_service`).
    """

    model_config = SettingsConfigDict(env_prefix="REPORT_VIEWER_", case_sensitive=False)

    # HTTP
    host: str = "0.0.0.0"
    port: int = 8000
    log_level: str = "info"

    # Postgres
    database_url: str = (
        "postgresql://report_viewer:report_viewer@localhost:5432/report_viewer"
    )

    # Trino (read-only analytics instance — user JWT pass-through)
    trino_host: str = "trino"
    trino_port: int = 8080
    trino_scheme: str = "http"
    trino_catalog: str = "delta"
    trino_schema: str = "default"
    trino_ca_cert: str | None = None

    # No trino-rw / write-side config — the service never writes to
    # Trino. Searches are saved SQL in Postgres; rows are evaluated on
    # demand against the read-only Trino instance.

    # Auth — Phase 0 uses a shared header secret. Phase 2 replaces this
    # with Keycloak JWT validation; the dev_shared_secret stays available
    # as an opt-in escape hatch for in-cluster smoke tests.
    dev_shared_secret: str = ""
    keycloak_jwks_url: str = ""
    keycloak_audience: str = "report-viewer-service"
    # Expected `iss` claim — Keycloak realm URL. Empty disables iss check
    # (useful for tests). The Ansible role sets this to
    # `{keycloak_realm_url}` so it matches what voila / OWUI mint.
    keycloak_issuer: str = ""

    # Search behavior
    search_ttl_days: int = 30

    # CSP — comma-separated list of origins that are allowed to embed the
    # viewer in an iframe. The default lets the Scout chat ingress embed
    # us; tighten per env via env var. Empty disables the CSP header.
    csp_frame_ancestors: str = "https://chat.dev02.tag.rcif.io"

    # OWUI Postgres URL — receiver writes iframe-sandbox UI defaults
    # into OWUI's "user".settings JSON column on signup. Empty disables
    # the webhook (returns 503). See ADR 0026.
    owui_database_url: str = ""
    # Shared secret OWUI sends in X-Scout-Webhook-Secret. Empty skips
    # verification.
    owui_webhook_secret: str = ""


settings = Settings()
