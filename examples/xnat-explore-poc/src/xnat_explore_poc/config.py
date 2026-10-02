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
    # /healthz. Fronted by an Ingress with the COOP: unsafe-none middleware,
    # since it's opened as a popup from OWUI's sandboxed chat embed (#739).
    # A separate port/app from the internal one (see app.py) so the public
    # listener structurally cannot reach /invoke at all, regardless of
    # NetworkPolicy - not just a defense-in-depth label.
    landing_page_port: int = 8080

    # This service's own public base URL (e.g. https://xnat-explore-poc-demo.<host>) -
    # what /invoke's response points at. Self-hosted specifically so this
    # PoC doesn't need to touch a real XNAT deployment's own Ingress/COOP
    # config just to demonstrate the popup mechanism end to end.
    landing_base_url: str = "http://localhost:8080"

    # Shared secret report-viewer sends as X-Report-Viewer-Action-Token,
    # sourced from a real Secret (helm/xnat-explore-poc/templates/secret.yaml),
    # not a plain values-driven env var. Still a single static shared
    # secret authenticating only this one endpoint, not real
    # service-to-service auth (mTLS, mesh identity, OIDC client
    # credentials) - replace before this leaves prototype status. Proves
    # only that the caller knows this value, not who the end user is -
    # see assertion_key below for that.
    invoke_token: str = ""

    # Verifies X-Report-Viewer-User-Assertion, a short-lived JWT report-viewer
    # signs with a key DIFFERENT from invoke_token (see actions-secret.yaml on
    # report-viewer's side) - deliberately separate because invoke_token
    # travels on every call and can leak via logs, while this key never goes
    # over the wire. Required: an App that skips verifying this has no
    # independent way to know who an invocation is actually for, and is
    # trusting report-viewer's own gating never has a bug.
    assertion_key: str = ""

    # Keycloak group the asserted caller must be a member of, or empty to
    # skip this check (the assertion's signature/expiry are still verified
    # either way). Matches whatever requiredGroup this action is configured
    # with on report-viewer's side - not read from anywhere automatically,
    # since this App has no notion of report-viewer's action catalog.
    required_group: str = ""


settings = Settings()
