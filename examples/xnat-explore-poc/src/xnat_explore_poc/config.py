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
    # Proves only that the caller knows this value, not who the end user
    # is - see assertion_key below for that. Still a single static shared
    # secret, not real service-to-service auth (mTLS, mesh identity, OIDC
    # client credentials) - replace before this leaves prototype status.
    invoke_token: str = ""

    # Verifies X-Report-Viewer-User-Assertion, a short-lived JWT signed
    # with a key DIFFERENT from invoke_token - see
    # scout_report_viewer.actions.mint_user_assertion's docstring (on
    # report-viewer's side) for why. Required: an App that skips verifying
    # this has no independent way to know who an invocation is actually
    # for.
    assertion_key: str = ""

    # Keycloak client role the asserted caller must hold, or empty to skip
    # this check (the assertion's signature/expiry are still verified
    # either way). Matches whatever requiredRole this action is configured
    # with on report-viewer's side - not read from anywhere automatically,
    # since this App has no notion of report-viewer's action catalog.
    required_role: str = ""


settings = Settings()
