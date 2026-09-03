from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


ALLOWED_JWT_ALGS: tuple[str, ...] = ("RS256",)


class Settings(BaseSettings):
    """Service-wide config, populated from the environment.

    All defaults are dev-friendly; production values come from the Helm
    chart's env block (rendered by `ansible/roles/report_viewer`).
    """

    model_config = SettingsConfigDict(env_prefix="REPORT_VIEWER_", case_sensitive=False)

    # HTTP
    host: str = "0.0.0.0"
    port: int = 8000
    log_level: str = "info"

    external_url: str

    # Postgres
    database_url: str = (
        "postgresql://report_viewer:report_viewer@localhost:5432/report_viewer"
    )

    # Trino connection
    trino_host: str = "trino"
    trino_port: int = 8080
    trino_scheme: str = "http"
    trino_catalog: str = "delta"
    trino_schema: str = "default"
    trino_ca_cert: str | None = None
    trino_auth_token_url: str = ""
    trino_auth_client_id: str = "report_viewer_svc"
    trino_auth_client_secret: str = ""

    # OIDC settings for inbound JWT validation.
    oidc_jwks_url: str = ""
    oidc_audience: str = "report-viewer"
    oidc_issuer: str = ""

    # Shared secret Traefik injects; the header path is refused unless it matches.
    gateway_secret: str = ""

    # Chat origin the SPA postMessages to (iframe height + Discuss/Refine).
    # Rendered into GET /api/config; empty disables cross-frame messaging.
    chat_origin: str = ""

    # Max rows returned by the full-cohort fetch (GET /rows). The SPA holds
    # these in memory and does sort/filter/paginate client-side. A larger cohort
    # is truncated and the SPA shows a "showing first N" banner.
    max_cohort_rows: int = 50000

    # Issue #628 PoC: in-cluster URL for Superset's internal
    # /internal/chat-cohort-export endpoint (routes/searches.py). Not the
    # external oauth2-proxy-fronted URL - this call is service-to-service.
    superset_internal_url: str = "http://superset.scout-analytics.svc.cluster.local:8088"

    # Issue #628 PoC: Superset's own external (browser-facing) URL, used to
    # build the returned explore_url. Superset has its own ingress
    # host/subdomain (ansible/roles/superset/templates/values.yaml.j2), not
    # a path under report-viewer's or the platform's shared external_url.
    superset_external_url: str = "https://superset.example.org"

    @model_validator(mode="after")
    def _issuer_required_with_jwks(self) -> "Settings":
        if self.oidc_jwks_url and not self.oidc_issuer:
            raise ValueError(
                "REPORT_VIEWER_OIDC_ISSUER must be set when "
                "REPORT_VIEWER_OIDC_JWKS_URL is configured"
            )
        return self


settings = Settings()
