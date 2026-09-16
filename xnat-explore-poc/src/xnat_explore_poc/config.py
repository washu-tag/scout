from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """All defaults are dev-friendly; production values come from the Helm
    chart's env block."""

    model_config = SettingsConfigDict(
        env_prefix="XNAT_EXPLORE_POC_", case_sensitive=False
    )

    host: str = "0.0.0.0"
    port: int = 8000

    # Bundled XNAT's browser-facing URL - what /invoke's response points at.
    xnat_base_url: str = "https://xnat.example.org"

    # Shared secret report-viewer sends as X-Report-Viewer-Action-Token.
    # PoC only, same
    # pattern (and same caveat) as the earlier chat-cohort-export PoC's
    # CHAT_COHORT_EXPORT_TOKEN: authenticates only this one endpoint,
    # replace with real service-to-service auth before this leaves
    # prototype status.
    invoke_token: str = ""


settings = Settings()
