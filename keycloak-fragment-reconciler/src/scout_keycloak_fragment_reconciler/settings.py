"""Process configuration, read from the environment as Settings is built."""

from typing import Annotated

from pydantic import Field, ValidationError, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict, SettingsError

ENV_PREFIX = "KEYCLOAK_FRAGMENT_RECONCILER_"

# The label that makes a ConfigMap a fragment, mirroring ADR 0034's chip label.
FRAGMENT_LABEL = "keycloak.scout.xnat.org/fragment"

# pydantic-settings JSON-decodes list-typed fields out of the environment
# before validators run, and raises if that fails. These take the
# comma-separated form a Helm template renders naturally instead.
CommaList = Annotated[list[str], NoDecode]


class Settings(BaseSettings):
    # Ignored, not forbidden: the chart also sets _LOG_LEVEL, which main reads
    # before settings exist.
    model_config = SettingsConfigDict(env_prefix=ENV_PREFIX, extra="ignore")

    # --- Keycloak -------------------------------------------------------
    keycloak_url: str = "http://keycloak-service:8080"
    realm: str = "scout"
    client_id: str = "fragment_reconciler_svc"
    client_secret: str = ""

    # --- What a fragment may ask for ------------------------------------
    # Grant targets are checked against this list rather than against any realm
    # role: otherwise a fragment could name `default-roles-scout` and hand its
    # role to every user in the realm.
    tier_roles: CommaList = ["scout-user", "scout-admin"]
    # Redirect URIs and app URLs must sit under this host, or a fragment's
    # client becomes a token-exfiltration redirect.
    server_hostname: str = ""

    # --- Discovery ------------------------------------------------------
    fragment_label: str = FRAGMENT_LABEL
    # Empty means every namespace. A read filter, never a permission boundary --
    # the grant is cluster-wide either way.
    watched_namespaces: CommaList = []

    # --- Timing ---------------------------------------------------------
    # The authoritative pass, and the only one that may infer an orphan. -1
    # disables it, leaving witnessed deletions as the only GC trigger.
    resync_seconds: int = 300
    # Absence tolerated before a fragment's client is deleted. Held in memory,
    # so a restart restarts the clock -- a floor rather than a guarantee. Bias
    # long: deleting early is an outage for a running app, while waiting leaves
    # an inert client only a departed app could have used.
    orphan_grace_seconds: int = 300
    # A watch fires once per object written, so a burst lands together.
    debounce_seconds: float = 2.0

    # --- Process --------------------------------------------------------
    port: int = 8080
    # Log every write that would happen and perform none of them.
    dry_run: bool = False

    @field_validator("tier_roles", "watched_namespaces", mode="before")
    @classmethod
    def _split_list(cls, value: object) -> object:
        """Accept a comma-separated string, which is what a chart renders.

        An empty string is the empty list, not `['']` -- one empty namespace
        name would match nothing and silently disable discovery. A JSON-looking
        value is refused rather than split, because splitting it yields
        plausible-looking junk that surfaces much later as a role that never
        matches.
        """
        if isinstance(value, str):
            if value.strip().startswith("["):
                raise ValueError(
                    "expected a comma-separated list, not JSON "
                    f"(got {value.strip()[:40]!r})"
                )
            return [part.strip() for part in value.split(",") if part.strip()]
        return value

    @property
    def label_selector(self) -> str:
        return f"{self.fragment_label}=true"

    def watches(self, namespace: str) -> bool:
        return not self.watched_namespaces or namespace in self.watched_namespaces

    def __init__(self, **values: object) -> None:
        # A misconfigured pod should name the wrong variable and stop, not
        # print a pydantic traceback. SettingsError is caught too, because
        # pydantic-settings raises it before the model for anything it cannot
        # decode out of the environment.
        try:
            super().__init__(**values)
        except ValidationError as exc:
            raise SystemExit("; ".join(_problems(exc))) from None
        except SettingsError as exc:
            raise SystemExit(f"{ENV_PREFIX}*: {exc}") from None


class RequiredSettings(Settings):
    """Settings plus the two fields that have no safe default.

    Split so tests and golden-file checks can build a `Settings` without
    inventing a credential. `main` uses this one.
    """

    client_secret: str = Field(min_length=1)
    server_hostname: str = Field(min_length=1)


def _problems(exc: ValidationError) -> list[str]:
    """Name the environment variable an operator set, not the field behind it."""
    out = []
    for error in exc.errors():
        field = str(error["loc"][0]) if error["loc"] else "(unknown)"
        name = field if field.startswith(ENV_PREFIX) else ENV_PREFIX + field.upper()
        out.append(f"{name}: {error['msg']}")
    return out
