from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """All defaults are dev-friendly; production values come from the Helm
    chart's env block."""

    model_config = SettingsConfigDict(
        env_prefix="XNAT_EXPLORE_POC_", case_sensitive=False
    )

    host: str = "0.0.0.0"
    # Internal-only: /invoke and /healthz. NetworkPolicy-restricted to
    # report-viewer's own pod - never fronted by an Ingress.
    port: int = 8000
    # Public: the landing page /invoke's response points at, plus its own
    # /healthz. A separate port/app from the internal one (see app.py) so
    # the public listener structurally cannot reach /invoke at all,
    # regardless of NetworkPolicy. Fronted by an Ingress needing COOP:
    # unsafe-none - see security-headers-sameorigin-popups in
    # ansible/roles/traefik/tasks/main.yaml for why.
    landing_page_port: int = 8080

    # This service's own public base URL (e.g. https://xnat-explore-poc-demo.<host>) -
    # what /invoke's response points at. Self-hosted so this PoC doesn't
    # need to touch real XNAT's own Ingress/COOP config to demonstrate the
    # popup mechanism end to end.
    landing_base_url: str = "http://localhost:8080"

    # Shared secret report-viewer sends as X-Report-Viewer-Action-Token,
    # sourced from a real Secret (helm/xnat-explore-poc/templates/secret.yaml).
    # The only thing this endpoint checks - sufficient because /invoke is
    # structurally unreachable except from report-viewer's own pod (see
    # port/landing_page_port above and this chart's networkpolicy.yaml), so
    # the realistic way this secret alone could be misused requires network
    # access a plain leaked value doesn't grant. report-viewer itself
    # already enforces role-gating before ever calling this endpoint
    # (scout_report_viewer.actions.list_actions) - this App doesn't
    # independently re-verify identity or role.
    invoke_token: str = ""


settings = Settings()
