# Authentication Reference

This is an admin- or developer-focused doc on the mechanisms behind authentication. For the steps to get a user account, see [Authentication](../user/authentication.md).

Authentication (who you _are_) is distinct from Authorization (what you can _do_ or _see_). This page covers the former; for the latter see [Data Authorization](../user/data_authorization.md).

## The pieces

| Component | Role | Trusted by |
| --- | --- | --- |
| Your institution's identity provider | The ultimate source of your identity, and where your password gets checked | Keycloak |
| Keycloak | A single-sign-on (SSO) service installed within Scout. Federates to the IdP, holds Scout accounts, issues tokens | Every Scout service |
| OAuth2 Proxy | Sits in front of every service, letting approved Scout users pass and redirecting unapproved users back to Keycloak | Traefik (the reverse proxy) |
| Each service's own Keycloak client | Defines roles that service understands | that service |

Here is an example diagram showing the auth flow for a request that arrives at Scout's "ingress", for a user who intends to navigate to Superset.

```
  Browser
     │
     │  1  GET https://superset.<scout-host>
     ▼
  Traefik ingress
     │
     │  2  ForwardAuth ──────────────────────────►  OAuth2 Proxy
     │                                                   │
     │         ┌──── no session yet ───────────────────── ┘
     │         ▼
     │      Keycloak  ──►  your institution's IdP  ──►  Keycloak
     │         │                                           │
     │         └──── 3  approved?  (oauth2-proxy-user) ──── ┘
     │                       │
     │            ┌──────────┴───────────┐
     │          yes                     no
     ▼            │                      │
  4  request reaches Superset     "Registration Pending"
              │
              │  5  Superset runs its own OIDC login against its own
              │     Keycloak client and receives a token naming its roles
              ▼
        the user is in
```

The first step for a request to any Scout service is to redirect to OAuth2 Proxy to check if the request has an active logged-in session, then redirect to Keycloak for login if necessary.

Keycloak sends that login request to whatever Identity Provider (IdP) is configured for your site, typically one managed by your institution. You log in with the IdP and Keycloak gets back information about your user. Keycloak then checks if your user account is approved to use Scout. Up to this point there has been no decision made about _what_ your user is allowed to do, simply _who_ you are and whether you can be on Scout at all.

After this, the request gets redirected back to its original service. That service wants to know what you're allowed to do so redirects your request back to Keycloak for login. At this point, Keycloak knows you're already logged in and does know who you are, so it creates an authorization code for that service which rides along with your request. Once the authorized request hits the service's backend, it exchanges the auth code for a token containing your relevant permissions. The service knows how to read those permissions and knows what that means according to its own internal data and permissions model, so grants your user access to the relevant data.

## What's in a Scout token

Tokens are JSON Web Tokens: signed, not encrypted. An abridged ID token:

```json
{
  "iss": "https://keycloak.scout.example.edu/realms/scout",
  "aud": "my-service",
  "sub": "8f14e45f-ceea-467a-9a1b-2d4f7e1c3b90",
  "exp": 1758210000,
  "preferred_username": "jdoe",
  "email": "jdoe@example.edu",
  "name": "Jane Doe",
  "groups": ["my-service-user"]
}
```

| Claim | Use it for |
| --- | --- |
| `sub` | The stable internal user id. Key your own records on this. |
| `preferred_username` | The human-readable identity. What Trino and the notebooks key on. |
| `email`, `name` | Display. Both can change; never key on them. |
| `groups` | The roles the user holds **in this service** — see below. |
| `iss`, `aud`, `exp` | Verification, not identity. |

Access tokens carry the same claims, but check `azp` rather than `aud` on them; see [validating tokens](../customize/service-authentication.md#4-read-the-token-and-act-on-roles).

:::{warning}
`groups` holds *role* names, not Keycloak group names. The claim is named `groups` for historical reasons and is populated per-service; see the next section.
:::

## Users, groups, tiers, and roles

An administrator places each user in one Keycloak group — `scout-user` or `scout-admin`. Everything else follows from that membership by two different routes:

```
  Keycloak group
  (an administrator puts the user here)
        │
        ├── carries client roles directly ────►  oauth2-proxy-user
        │   (Scout's built-in services)          superset_gamma
        │                                        jupyterhub-user
        │
        └── carries the tier realm role ──────►  scout-user
                                                      │
                                                      │  composite expansion
                                                      │  (added by an app's own
                                                      │   configuration)
                                                      ▼
                                                 my-service-user  ◄── your service's role
```

Either route ends the same way: the roles belonging to a given service appear in the `groups` claim of *that service's* token, and nowhere else. A service cannot see another service's roles.

A user in a group that grants none of your service's roles arrives with an empty `groups` claim. They are authenticated; they simply hold no role.

:::{warning}
**The two tiers are siblings, not a hierarchy.** `scout-admin` does not include `scout-user`. A service that gates ordinary access on its `-user` role alone will lock out every administrator. Grant both roles to the admin tier, or test for either.
:::

Keycloak's own [roles and groups guide](https://www.keycloak.org/docs/latest/server_admin/index.html#assigning-permissions-using-roles-and-groups) covers composite roles in general terms.

## Becoming a Scout user

1. First login through the IdP creates a Keycloak account with no group membership.
2. The user must accept the terms of use, then sees "Registration Pending".
3. An administrator approves their account and adds them to the `scout-user` and/or `scout-admin` groups.
4. Access begins on their next login.

Administrators do this from the user console on the Scout launchpad. The same console sets the per-user data-access attributes described in [Data Authorization](../operate/data_authorization.md).

## Sessions and expiry

Keycloak holds one SSO session per user; each service holds its own session on top of it. A service session must not outlive the Keycloak session that produced it, or it will hold credentials it can no longer refresh. Redeploying Keycloak ends every SSO session, so everyone logs in again.

## Where authentication ends

A token says who the user is and what they may do inside a service. It says nothing about which rows and columns they may read — that is enforced at the query layer, per user, and is configured separately. See [Data Authorization](../operate/data_authorization.md).

## Learn more

Scout's identity layer is standard OpenID Connect; nothing here is Scout-specific protocol.

| | |
| --- | --- |
| [OAuth 2.0 Simplified](https://www.oauth.com/) | The friendliest walkthrough of the authorization code flow, tokens, and PKCE |
| [How OpenID Connect Works](https://openid.net/developers/how-connect-works/) | Short, diagrammed introduction to OIDC and ID tokens |
| [Keycloak: OIDC endpoints](https://www.keycloak.org/securing-apps/oidc-layers) | Keycloak's OIDC endpoints (authorization, token, JWKS, introspection, logout, and more) and the grant types it supports |
