"""Process configuration, read from the environment as Settings is built."""

from typing import Annotated

from pydantic import Field, ValidationError, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

ENV_PREFIX = "KEYCLOAK_FRAGMENT_RECONCILER_"

FRAGMENT_LABEL = "keycloak.scout.xnat.org/fragment"

# Kubernetes namespace names are lowercase, so this cannot collide with one.
WATCH_ALL = "ALL"

# pydantic-settings JSON-decodes list-typed fields out of the environment;
# these take the comma-separated form a Helm template renders instead.
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
    # An allowlist, not any realm role: a fragment naming `default-roles-scout`
    # would otherwise hand its role to every user in the realm.
    tier_roles: CommaList = ["scout-user", "scout-admin"]
    # Redirect URIs and app URLs must sit under this host, or a fragment's
    # client becomes a token-exfiltration redirect.
    server_hostname: str = ""

    # --- Discovery ------------------------------------------------------
    fragment_label: str = FRAGMENT_LABEL
    # Namespace names, or the lone `WATCH_ALL`. Empty means none, so an
    # unconfigured install reconciles nothing rather than everything.
    watched_namespaces: CommaList = []

    # --- Timing ---------------------------------------------------------
    # Drift repair for what nothing reports: a rotated credential, an
    # admin-console edit, a reaped tier edge. Non-positive parks on the watch.
    resync_seconds: int = Field(default=300, ge=-1)
    # Absence tolerated before a fragment's client is deleted. Held in memory,
    # so a restart restarts the clock -- a floor, not a guarantee.
    orphan_grace_seconds: int = Field(default=300, ge=0)
    # A watch fires once per object written, so a burst lands together.
    debounce_seconds: float = Field(default=2.0, ge=0)

    # --- Process --------------------------------------------------------
    # The probes hit a fixed containerPort, and the pod runs as uid 65532.
    port: int = Field(default=8080, ge=1024, le=65535)
    dry_run: bool = False

    @field_validator("tier_roles", "watched_namespaces", mode="before")
    @classmethod
    def _split_list(cls, value: object) -> object:
        """Accept a comma-separated string, which is what a chart renders.

        An empty string is the empty list, not `['']` -- an entry no namespace
        or role can ever match. A JSON-looking value is refused rather than
        split, because splitting it yields plausible-looking junk that surfaces
        much later as a role that never matches.
        """
        if isinstance(value, str):
            if value.strip().startswith("["):
                raise ValueError(
                    "expected a comma-separated list, not JSON "
                    f"(got {value.strip()[:40]!r})"
                )
            return [part.strip() for part in value.split(",") if part.strip()]
        return value

    @field_validator("watched_namespaces")
    @classmethod
    def _all_stands_alone(cls, value: list[str]) -> list[str]:
        """`ALL,foo` is a contradiction, and reading it as either half is a
        guess at which one the operator meant."""
        if WATCH_ALL in value and len(value) > 1:
            raise ValueError(
                f"{WATCH_ALL} means every namespace, so it cannot be combined "
                f"with namespace names (got {','.join(value)!r})"
            )
        return value

    @property
    def label_selector(self) -> str:
        return f"{self.fragment_label}=true"

    @property
    def watches_all(self) -> bool:
        return WATCH_ALL in self.watched_namespaces

    def watches(self, namespace: str) -> bool:
        return self.watches_all or namespace in self.watched_namespaces

    def __init__(self, **values: object) -> None:
        # A misconfigured pod should name the wrong variable, not print a
        # pydantic traceback.
        try:
            super().__init__(**values)
        except ValidationError as exc:
            raise SystemExit("; ".join(_problems(exc))) from None


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
