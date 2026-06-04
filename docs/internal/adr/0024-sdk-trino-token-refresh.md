# ADR 0024: Token Refresh for SDK Trino Access

**Date**: 2026-06-04
**Status**: Accepted
**Decision Owner**: TAG Team

## Context

[ADR 0022](0022-trino-auth-and-impersonation.md) establishes that notebook and
playbook code reaches Trino through short-lived bearer tokens — the end user's
own Keycloak access token (JupyterHub, JWT pass-through) or a `voila_svc`
service-principal token (Voila, JWT + impersonation). Requirement 4 of that ADR
is explicit: **"No bespoke OIDC-token refresh in user code."** Callers do
`scout.query(...)` / `scout.connect()` and get a working Trino session; the
`scout` SDK owns keeping the bearer valid.

Two facts make this non-trivial:

- **Tokens are short-lived.** End-user Keycloak access tokens default to 300s
  (`keycloak_access_token_lifespan`); `voila_svc` tokens ~4h. A notebook left
  open and queried intermittently crosses many expiry boundaries.
- **Trino's JWT validation allows zero clock skew.** Observed failure:
  `JWT expired 75984 milliseconds ago … Allowed clock skew: 0 milliseconds`.
  A token that is even slightly past expiry at the moment Trino checks it is
  rejected with HTTP 401.

The SDK does not hold the long-lived refresh credential — by design (ADR 0022,
and the RFC 9700 principle of keeping the refresh token off the exposed
surface). The refresh token lives Hub-side (Jupyter) or is the client secret in
a Kubernetes Secret (Voila); the kernel only ever holds a short-lived access
token. So "refresh" here means **obtain a fresh access token from the custodian
before / when the current one stops working**, not "run the OAuth refresh grant
in the kernel."

This is a well-trodden problem (see References); the industry consensus is a
**layered proactive + reactive** strategy with single-flight concurrency. We
adopt that consensus, adapted to our custodian-pull architecture.

## Decision

**Keep the bearer valid with four cooperating layers; proactive refresh
prevents almost all expiries, a reactive retry self-heals the residual.**

### 1. Custodian-side proactive rotation

The thing that issues fresh access tokens must rotate them *before* expiry, not
just re-validate them.

- **Voila**: the SDK's `_VoilaSvcProvider` mints `voila_svc` tokens directly via
  `client_credentials`, so it controls rotation itself (layer 2).
- **Jupyter**: the custodian is JupyterHub. Stock oauthenticator only exchanges
  the refresh token *after* the access token has expired; when its
  `auth_refresh_age` gate opens while the token is still alive it re-validates
  and resets the refresh clock **without** rotating — so the Hub can hand a
  kernel a token that is seconds from (or past) expiry. We install an
  oauthenticator `refresh_user_hook` (`eager_token_refresh`, supported ≥17.3;
  z2jh 4.3.5 ships 17.4.0) that exchanges the refresh token whenever
  `refresh_user` runs past the token's **half-life**, making the Hub behave the
  way its own docs promise (`/hub/api/user` "always returns a valid token").

### 2. SDK-side proactive refresh

- A single cached bearer per provider, re-fetched once less than **⅕ of the
  token's lifetime** remains (`_REFRESH_BEFORE_EXPIRY_FRACTION`; lifetime =
  `exp − iat`, both read from the JWT), so the buffer scales with the lifespan
  rather than being a fixed offset. Tokens with no `iat` fall back to a fixed
  60s (`_REFRESH_BEFORE_EXPIRY_SECONDS`).
- The bearer is attached via a **dynamic** auth callable
  (`_DynamicJWTAuthentication` → `_DynamicBearerAuth`) that re-reads the cached
  token on every HTTP request. This lets one long-lived SQLAlchemy engine
  survive token rotation without `engine.dispose()` (connection pool + Trino
  session cookies are preserved).

### 3. SDK-side reactive retry

`query()` wraps execution in `_with_auth_retry`: on a Trino HTTP **401**
(detected by walking the exception chain for `trino.exceptions.HttpError` with
`error 401`, since SQLAlchemy/pandas wrap the original), it drops the cached
bearer (`_identity.invalidate()`) and retries **once**. The re-fetch yields a
fresh token (Voila re-mints; Jupyter re-pulls from the Hub, which rotates via
the layer-1 hook), so a transient 401 from clock skew or an in-flight expiry
never reaches the notebook. **403 is deliberately not retried**: it is an
authorization denial (an OPA decision against an authenticated identity), so a
fresh token for the same identity wouldn't change the outcome — retrying would
only mask the denial behind a duplicate request. `connect()` returns a raw
DB-API connection whose execution the caller drives, so it carries proactive
refresh but not this reactive wrapper.

### 4. Concurrency: in-process single-flight

Each provider serializes mint/fetch under a `threading.Lock`. One user per
kernel, so a process-local lock is sufficient — no distributed lock is needed
(contrast multi-instance services, where single-use rotating refresh tokens
force a shared lock to avoid reuse-detection lockouts).

### Refresh-gate / lifespan coupling

`auth_refresh_age` must reopen the Hub's refresh gate *before* the SDK's
re-fetch window, or the gate stays shut during exactly the window that matters.
Stated as fractions of the lifespan: the hook rotates at the half-life (½) and
the SDK re-fetches in the last fifth (⅕), so the gate must satisfy
`gate ≤ ½ − ⅕ = 3⁄10` of the lifespan. We set `auth_refresh_age =
keycloak_access_token_lifespan // 5` (⅕), inside that bound with a 1⁄10 margin —
and because every term is a fraction of the lifespan, it holds for **any**
lifespan with no floor or per-value tuning. (At the 300s default that's 60s.)
`keycloak_access_token_lifespan` is a single `scout_common` variable that drives
**both** the realm's `accessTokenLifespan` and the Hub's `auth_refresh_age`, so
the two can't drift.

## Alternatives Considered

### Reactive-only (refresh on 401, no proactive layer)
Rejected as the primary mechanism: adds a failed-request + retry latency to the
first query after every expiry, and under any concurrency produces correlated
401s. Kept only as the backstop (layer 3).

### Proactive-only (no reactive retry)
This is what we shipped first, and it leaves a boundary gap: Trino's zero-skew
check rejects a token the SDK still considered "valid enough," with no recovery
but to re-run the cell. The reactive layer closes it.

### Tune `auth_refresh_age` alone (no eager-rotation hook)
Insufficient: stock oauthenticator re-validates rather than rotates when the
gate opens on a live token, so no gate value guarantees the Hub returns a token
with enough remaining lifetime. The hook is what makes the custodian proactive;
the gate value only controls how often it is consulted.

### Raise the access-token lifespan
Reduces boundary crossings but weakens revocation latency (a disabled user keeps
a working token longer) and doesn't eliminate the gap. The AuthZ design favors
short lifespans + fast propagation (OPA bundle, ADR 0021).

### RFC 8693 token exchange in the kernel
Rejected in ADR 0022: the kernel runs the user's own code with the user's own
token, so there is no confused deputy for exchange to resolve.

### Distributed lock for refresh
Unnecessary: one user per kernel process. Revisit only if a future consumer
shares a token cache across concurrent users.

## Consequences

- A notebook can be left open across many token lifetimes and keep querying;
  the user is not asked to re-run cells around the 5-minute boundary.
- Residual, correct failure modes remain (not bugs): Keycloak unreachable at
  refresh time, or the 8h SSO session ending (refresh token dies) — both
  surface as an error directing the user to log in to JupyterHub again, and the
  `_JupyterHubUserProvider` already warns once when the Hub returns a near-expiry
  token.
- The layer-1 Hub hook is a workaround for stock-oauthenticator behavior. It is
  version-gated and deletable if/when upstream makes refresh proactive; an
  upstream issue with the dev04 trace should be filed.
- `connect()` callers on the rare in-flight-expiry path re-run the failing
  statement themselves; documented in its docstring.

## Implementation Notes

- SDK: `sdk/python/src/scout/_query.py` (`_is_unauthorized`, `_with_auth_retry`,
  dynamic auth, `trust_env=False` CA pinning per the package-proxy footgun) and
  `_identity.py` (providers, `_is_near_expiry`, per-provider + module-level
  `invalidate()`).
- Hub hook + gate: `ansible/roles/jupyter/templates/values.yaml.j2`
  (`eagerTokenRefresh` extraConfig, `auth_refresh_age`).
- Lifespan variable: `ansible/roles/scout_common/defaults/main.yaml`
  (`keycloak_access_token_lifespan`), consumed by
  `ansible/roles/keycloak/templates/scout-realm.json.j2`.
- Tests: `sdk/python/tests/test_query.py` (401 detection direct/chained,
  403-and-non-auth excluded, retry-once, no-retry-on-403, provider
  invalidation) and
  `sdk/python/tests_contract/` (real trino-python-client X-Trino-User behavior).

## References

- [RFC 9700 — Best Current Practice for OAuth 2.0 Security](https://datatracker.ietf.org/doc/rfc9700/) (Jan 2025): refresh-token rotation, reuse detection, sender-constraining, keeping the refresh credential off the exposed surface.
- Kubernetes bound service-account tokens ([KEP-1205](https://github.com/kubernetes/enhancements/blob/master/keps/sig-auth/1205-bound-service-account-tokens/README.md)): kubelet proactively rotates projected tokens at **80% of TTL**; the app re-reads. Our custodian-pull design is the API-pull analogue.
- SPIFFE/SPIRE: agent proactively re-issues SVIDs before expiry; the SPIFFE Helper sidecar writes refreshed credentials and the app reloads.
- [ADR 0022](0022-trino-auth-and-impersonation.md) (identity propagation), [ADR 0021](0021-opa-user-attribute-distribution.md) (fast attribute propagation), [ADR 0017](0017-air-gapped-package-proxy.md) (the `REQUESTS_CA_BUNDLE` interaction the SDK pins around).
