"""Process configuration, read from the environment as Settings is built."""

from typing import Annotated

from pydantic import Field, ValidationError, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict, SettingsError

ENV_PREFIX = "KEYCLOAK_FRAGMENT_RECONCILER_"

# The label that makes a ConfigMap a fragment, mirroring ADR 0034's chip label.
FRAGMENT_LABEL = "keycloak.scout.xnat.org/fragment"

# The one watched_namespaces entry that is not a namespace name. Kubernetes
# namespace names are lowercase, so it cannot collide with a real one.
WATCH_ALL = "ALL"

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
    # Namespace names, or the lone value `ALL` for every namespace. Empty means
    # none, and is the default so that an unconfigured install reconciles
    # nothing rather than everything -- the chart withholds the cluster read
    # grant to match, so this is the one setting that is a permission boundary
    # as well as a read filter. Once non-empty it is only a read filter, the
    # grant being cluster-wide either way. Garbage collection is scoped to the
    # same list, so narrowing it leaves the clients it stops covering alone
    # rather than reading them as deleted.
    watched_namespaces: CommaList = []

    # --- Timing ---------------------------------------------------------
    # How often to re-read everything and repair what nothing reported: a
    # rotated credential, an admin-console edit, a reaped tier edge. -1 removes
    # the timer entirely and parks on the watch, so the realm is only ever
    # re-read when a fragment changes -- an orphan's grace period still comes
    # due, but drift is not looked for until something wakes the loop.
    resync_seconds: int = Field(default=300, ge=-1)
    # Absence tolerated before a fragment's client is deleted. Held in memory,
    # so a restart restarts the clock -- a floor rather than a guarantee. Bias
    # long: deleting early is an outage for a running app, while waiting leaves
    # an inert client only a departed app could have used.
    orphan_grace_seconds: int = Field(default=300, ge=0)
    # A watch fires once per object written, so a burst lands together.
    debounce_seconds: float = Field(default=2.0, ge=0)

    # --- Process --------------------------------------------------------
    # Non-privileged: the pod runs as uid 65532, and the probes hit a fixed
    # containerPort, so an ephemeral 0 would never go Ready.
    port: int = Field(default=8080, ge=1024, le=65535)
    # Log every write that would happen and perform none of them.
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
