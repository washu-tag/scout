"""Reading back what the realm says was last imported into it.

`pending_change` compares the composed realm with the last one this process
applied, and never looks at Keycloak. That is blind to the case this whole
design exists to close: something *else* wrote the realm. The Ansible auth play
still does exactly that, and afterwards the reconciler reports the realm up to
date while a fragment's client is gone.

keycloak-config-cli leaves a record. After every import it stores the
document's checksum in a realm attribute, so one admin read says whether the
last writer was us.

Two things about that checksum are load-bearing and neither is obvious from the
attribute:

- it is taken **after** variable substitution, over the resolved document, not
  the one we published; and
- it is `sha256Hex(content + salt)`, where the salt is empty only while
  `import.behaviors.user-update-ignored-properties` is unset.

So the reconciler does not compute the expected value -- it reads back what
config-cli actually wrote once, after each apply, and compares against that.
One extra GET per apply buys independence from both of those, and from
whatever else a future config-cli folds into the hash. It also cannot loop: an
expectation that came from the realm always matches the realm until something
changes it.
"""

import logging
import time
from collections.abc import Callable

import httpx2 as httpx

log = logging.getLogger("app-manager")

# `import-checksum-{cacheKey}`, and Scout never sets IMPORT_CACHE_KEY.
CHECKSUM_ATTRIBUTE = "de.adorsys.keycloak.config.import-checksum-default"

# The bootstrap admin config-cli itself authenticates as.
ADMIN_REALM = "master"
ADMIN_CLIENT_ID = "admin-cli"

TIMEOUT_SECONDS = 10.0
# Re-authenticate this long before the token expires, so a slow read cannot
# land on the far side of the boundary.
EXPIRY_MARGIN_SECONDS = 30.0

# (username, password), or None when the admin Secret cannot be read.
Credentials = Callable[[], "tuple[str, str] | None"]


class KeycloakAdmin:
    """The one thing the reconciler asks Keycloak directly.

    Every failure is a warning and a None: not knowing whether the realm
    drifted must never stop a reconcile, and must never be mistaken for
    knowing that it did.
    """

    def __init__(self, base_url: str, realm: str, credentials: Credentials) -> None:
        self.base = base_url.rstrip("/")
        self.realm = realm
        self.credentials = credentials
        self._token = ""
        self._expires_at = 0.0

    def import_checksum(self) -> str | None:
        """The checksum config-cli recorded, or None if it cannot be read."""
        realm = self._realm()
        if realm is None:
            return None
        return (realm.get("attributes") or {}).get(CHECKSUM_ATTRIBUTE)

    def _realm(self) -> dict | None:
        token = self._access_token()
        if not token:
            return None
        try:
            response = httpx.get(
                f"{self.base}/admin/realms/{self.realm}",
                headers={"Authorization": f"Bearer {token}"},
                timeout=TIMEOUT_SECONDS,
            )
        except httpx.HTTPError as exc:
            log.warning("could not read realm %s from Keycloak: %s", self.realm, exc)
            return None
        if response.status_code == 401:
            # The token went stale mid-flight; one retry with a fresh one.
            self._expires_at = 0.0
            token = self._access_token()
            if not token:
                return None
            response = httpx.get(
                f"{self.base}/admin/realms/{self.realm}",
                headers={"Authorization": f"Bearer {token}"},
                timeout=TIMEOUT_SECONDS,
            )
        if response.status_code != 200:
            log.warning(
                "Keycloak returned %s reading realm %s",
                response.status_code,
                self.realm,
            )
            return None
        return response.json()

    def _access_token(self) -> str:
        if self._token and time.monotonic() < self._expires_at:
            return self._token
        credentials = self.credentials()
        if not credentials:
            log.warning("no Keycloak admin credentials; cannot check the live realm")
            return ""
        username, password = credentials
        try:
            response = httpx.post(
                f"{self.base}/realms/{ADMIN_REALM}/protocol/openid-connect/token",
                data={
                    "grant_type": "password",
                    "client_id": ADMIN_CLIENT_ID,
                    "username": username,
                    "password": password,
                },
                timeout=TIMEOUT_SECONDS,
            )
        except httpx.HTTPError as exc:
            log.warning("could not authenticate to Keycloak: %s", exc)
            return ""
        if response.status_code != 200:
            log.warning(
                "Keycloak refused the admin password grant with %s",
                response.status_code,
            )
            return ""
        payload = response.json()
        self._token = payload.get("access_token", "")
        self._expires_at = (
            time.monotonic()
            + float(payload.get("expires_in", 60))
            - EXPIRY_MARGIN_SECONDS
        )
        return self._token


__all__ = ["CHECKSUM_ATTRIBUTE", "KeycloakAdmin"]
