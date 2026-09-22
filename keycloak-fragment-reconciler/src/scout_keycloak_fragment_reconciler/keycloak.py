"""The Keycloak admin API, as narrowly as the reconciler needs it.

Knows nothing about fragments: every argument here is a Keycloak
representation or an identifier, and the mapping from a fragment to those lives
in `translate`. That boundary is what keeps the translator testable without a
server, and the one endpoint list here is what a Keycloak upgrade churns.

Admin API v1, which is the supported one -- v2 is still flagged experimental
(ADR 0037), and a realm-fragment concept is exactly what it does not have yet.

Two paths here were verified against the live 26.6.4 server rather than
assumed, because the design leans on both:

- `GET /roles/{tier}/composites/clients/{uuid}` returns just the named client's
  edges into a tier role. Without it, diffing one fragment's grants would mean
  reading every other fragment's, and a fragment's cost would grow with the
  number of installed apps.
- `POST /roles/{tier}/composites` is **additive**. It never replaces the set,
  so two fragments writing edges into the same tier role structurally cannot
  clobber each other. That is why arbitration only has to settle clientIds.

Errors are classified rather than raised uniformly: a 5xx or a timeout is worth
retrying on the next pass, and a 400 means the representation is wrong and
retrying it forever is noise.
"""

from __future__ import annotations

import logging
import time
import urllib.parse
from pathlib import Path

import httpx2 as httpx

log = logging.getLogger("keycloak-fragment-reconciler")

TIMEOUT_SECONDS = 15.0
# Re-authenticate once this fraction of the token's lifetime is left, so a slow
# call cannot land on the far side of the boundary. A fraction, not a constant,
# so the margin holds for whatever lifespan the realm is configured with -- the
# policy and value Scout's other client_credentials caches use (ADR 0024).
REFRESH_BEFORE_EXPIRY_FRACTION = 0.2
# Assumed token lifetime when the token endpoint omits `expires_in`.
DEFAULT_TOKEN_LIFETIME_SECONDS = 60.0
# How much of an error body to carry into the exception message.
ERROR_EXCERPT_CHARS = 400


def _seg(value: str) -> str:
    """One path segment, with the separators encoded rather than left to mean.

    `safe=""` is the whole point: the default `quote` leaves `/` alone, and
    httpx resolves the `..` segments that follow it before the request goes
    out, so a name is otherwise free to walk up out of its own collection --
    `/clients/{uuid}/roles/../../../roles/x` reaches the realm role `x`. The
    fragment contract already rejects such a name; this is the second lock, on
    the side that builds the URL. For a name the contract does accept this is a
    no-op, since `.`, `-` and `_` are never encoded.
    """
    return urllib.parse.quote(value, safe="")


def _uuid_from_location(location: str) -> str:
    """The uuid a creation's `Location` header ends in, or "" if it has none."""
    path = urllib.parse.urlparse(location).path.rstrip("/")
    _, sep, uuid = path.rpartition("/clients/")
    return uuid if sep and "/" not in uuid else ""


class KeycloakError(RuntimeError):
    def __init__(self, status: int, message: str):
        super().__init__(f"keycloak admin API {status}: {message}")
        self.status = status

    @property
    def retryable(self) -> bool:
        """Whether the next pass should try this again.

        0 is a transport failure. 401/403 are retryable on purpose: the usual
        cause is the realm not having been applied yet, or a credential rotated
        a moment ago -- both resolve without anyone editing a fragment. A 4xx
        that is neither is a bad representation, and repeating it is noise.
        """
        return self.status == 0 or self.status >= 500 or self.status in (401, 403, 429)


def _decoded(response: httpx.Response, context: str) -> object:
    """The body as JSON, with a decode failure classified like any other.

    A `ValueError` is what an undecodable body raises, and no handler between
    here and the run loop narrows to it, so unclassified it would end the pass
    rather than the one fragment. Status 0 for the same reason a transport
    failure is: the usual cause is something other than Keycloak answering.
    """
    try:
        return response.json()
    except ValueError as exc:
        raise KeycloakError(0, f"{context}: body is not JSON: {exc}") from None


class Admin:
    def __init__(
        self, base_url: str, realm: str, client_id: str, secret_path: str | Path
    ) -> None:
        self.base = base_url.rstrip("/")
        self.realm = realm
        self.client_id = client_id
        self._secret_path = Path(secret_path)
        self._token = ""
        self._expires_at = 0.0
        self._http = httpx.Client(timeout=TIMEOUT_SECONDS)

    # --- plumbing -------------------------------------------------------

    def _admin(self, path: str) -> str:
        return f"{self.base}/admin/realms/{self.realm}{path}"

    def _secret(self) -> str:
        """The credential, re-read on every authentication.

        A mounted Secret is rewritten in place when the credential rotates, so
        the value the process started with goes stale and nothing rolls the
        pod; `k8s.Client._headers` reads the projected service-account token
        the same way and for the same reason. An unreadable file is classified
        rather than raised as an `OSError`, which is no `httpx.HTTPError` and
        would escape every handler between here and the run loop.
        """
        try:
            return self._secret_path.read_text(encoding="utf-8").strip()
        except OSError as exc:
            raise KeycloakError(
                0, f"client credential at {self._secret_path} is unreadable: {exc}"
            ) from None

    def _access_token(self, *, force: bool = False) -> str:
        if self._token and not force and time.monotonic() < self._expires_at:
            return self._token
        url = f"{self.base}/realms/{self.realm}/protocol/openid-connect/token"
        secret = self._secret()
        try:
            response = self._http.post(
                url,
                data={
                    "grant_type": "client_credentials",
                    "client_id": self.client_id,
                    "client_secret": secret,
                },
            )
        except httpx.HTTPError as exc:
            raise KeycloakError(0, f"token endpoint unreachable: {exc}") from None
        if response.status_code != 200:
            raise KeycloakError(
                response.status_code,
                f"client_credentials refused for {self.client_id}: "
                f"{response.text[:ERROR_EXCERPT_CHARS]}",
            )
        payload = _decoded(response, "token endpoint")
        if not isinstance(payload, dict):
            raise KeycloakError(0, "token endpoint returned no access_token")
        self._token = payload.get("access_token", "")
        now = time.monotonic()
        lifetime = float(payload.get("expires_in", DEFAULT_TOKEN_LIFETIME_SECONDS))
        self._expires_at = max(
            now, now + lifetime * (1.0 - REFRESH_BEFORE_EXPIRY_FRACTION)
        )
        if not self._token:
            raise KeycloakError(0, "token endpoint returned no access_token")
        return self._token

    def _send(
        self, method: str, url: str, json: object, *, force_token: bool = False
    ) -> httpx.Response:
        token = self._access_token(force=force_token)
        try:
            return self._http.request(
                method, url, json=json, headers={"Authorization": f"Bearer {token}"}
            )
        except httpx.HTTPError as exc:
            raise KeycloakError(0, f"{method} {url}: {exc}") from None

    def _call(self, method: str, url: str, *, json: object = None) -> httpx.Response:
        response = self._send(method, url, json)
        if response.status_code != 401:
            return response
        # The token went stale mid-flight; one retry with a fresh one. Not a
        # loop: a second 401 is an authorization problem, and the usual one is
        # roles assigned to the service-account user but filtered out of its
        # token because they are not in the client's scope.
        return self._send(method, url, json, force_token=True)

    def _checked(
        self, method: str, path: str, *, json: object = None
    ) -> httpx.Response:
        response = self._call(method, self._admin(path), json=json)
        if response.status_code >= 400:
            raise KeycloakError(
                response.status_code, response.text[:ERROR_EXCERPT_CHARS]
            )
        return response

    def _request(self, method: str, path: str, *, json: object = None) -> object:
        response = self._checked(method, path, json=json)
        if not response.content:
            return None
        return _decoded(response, f"{method} {path}")

    def _get_or_none(self, path: str) -> object:
        try:
            return self._request("GET", path)
        except KeycloakError as exc:
            if exc.status == 404:
                return None
            raise

    # --- realm ----------------------------------------------------------

    def realm_role(self, name: str) -> dict | None:
        """A realm role, or None.

        Absence is retryable and never repaired: the tier roles belong to the
        base realm, and creating one here would make this service a writer of
        an object it must not own.
        """
        result = self._get_or_none(f"/roles/{_seg(name)}")
        return result if isinstance(result, dict) else None

    # --- clients --------------------------------------------------------

    def find_client(self, client_id: str) -> dict | None:
        query = urllib.parse.urlencode({"clientId": client_id})
        result = self._request("GET", f"/clients?{query}")
        # The filter is exact, but it is a search endpoint and returns a list.
        for client in result or []:
            if client.get("clientId") == client_id:
                return client
        return None

    def list_clients(self) -> list[dict]:
        """Every client in the realm. The read GC infers orphans from.

        No server-side filter on attributes exists, so the stamp is applied
        client-side. The realm has tens of clients, not thousands.
        """
        result = self._request("GET", "/clients")
        return list(result or [])

    def create_client(self, representation: dict) -> str:
        """Create, and return the new client's uuid.

        Keycloak answers 201 with no body and a Location header ending in the
        uuid. The read-back is only for a response that carries no usable
        Location: it costs a round trip, and it can transiently miss a client
        that was in fact created, which would fail an apply that succeeded.
        """
        response = self._checked("POST", "/clients", json=representation)
        uuid = _uuid_from_location(response.headers.get("Location", ""))
        if uuid:
            return uuid
        created = self.find_client(representation["clientId"])
        if not created:
            raise KeycloakError(
                0, f"created {representation['clientId']} but cannot read it back"
            )
        return created["id"]

    def update_client(self, uuid: str, representation: dict) -> None:
        self._request("PUT", f"/clients/{uuid}", json=representation)

    def delete_client(self, uuid: str) -> None:
        self._request("DELETE", f"/clients/{uuid}")

    def client_secret(self, uuid: str) -> str:
        """The live credential, so a rotation is detected by read-back.

        Cheaper and less stateful than the alternative, which is storing a hash
        of the credential in a client attribute -- that would put a derivative
        of a secret into the realm document's neighbourhood for no gain.
        """
        result = self._request("GET", f"/clients/{uuid}/client-secret")
        return (result or {}).get("value", "") if isinstance(result, dict) else ""

    # --- client roles ---------------------------------------------------

    def client_roles(self, uuid: str) -> list[dict]:
        result = self._request("GET", f"/clients/{uuid}/roles")
        return list(result or [])

    def create_client_role(self, uuid: str, representation: dict) -> None:
        self._request("POST", f"/clients/{uuid}/roles", json=representation)

    def delete_client_role(self, uuid: str, name: str) -> None:
        self._request("DELETE", f"/clients/{uuid}/roles/{_seg(name)}")

    # --- protocol mappers -----------------------------------------------

    def protocol_mappers(self, uuid: str) -> list[dict]:
        result = self._request("GET", f"/clients/{uuid}/protocol-mappers/models")
        return list(result or [])

    def create_protocol_mapper(self, uuid: str, representation: dict) -> None:
        self._request(
            "POST", f"/clients/{uuid}/protocol-mappers/models", json=representation
        )

    def update_protocol_mapper(self, uuid: str, mapper_id: str, rep: dict) -> None:
        self._request(
            "PUT", f"/clients/{uuid}/protocol-mappers/models/{mapper_id}", json=rep
        )

    def delete_protocol_mapper(self, uuid: str, mapper_id: str) -> None:
        self._request("DELETE", f"/clients/{uuid}/protocol-mappers/models/{mapper_id}")

    # --- composite (tier) edges -----------------------------------------

    def tier_edges_for_client(self, tier: str, client_uuid: str) -> list[dict]:
        """This client's roles that are already composed into `tier`.

        The per-client read, so a fragment's diff does not have to enumerate
        every other fragment's edges.
        """
        path = f"/roles/{_seg(tier)}/composites/clients/{client_uuid}"
        result = self._get_or_none(path)
        return list(result or [])

    def add_tier_edges(self, tier: str, roles: list[dict]) -> None:
        """Additive. Never replaces the set, so no fragment can clobber another."""
        self._request("POST", f"/roles/{_seg(tier)}/composites", json=roles)

    def remove_tier_edges(self, tier: str, roles: list[dict]) -> None:
        self._request("DELETE", f"/roles/{_seg(tier)}/composites", json=roles)
