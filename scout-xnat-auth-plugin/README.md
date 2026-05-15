# xnat-scout-auth-plugin

XNAT plugin that integrates XNAT into Scout's auth posture:

- **`HeaderTrustFilter`** — trusts oauth2-proxy's `X-Auth-Request-*`
  identity headers for browser-path traffic. Provisions the matching
  XNAT user on first sight via `UserProvisioningService`.
- **`BearerTokenFilter`** — currently a Phase A stub. Phase B will fill
  it in to validate Keycloak JWTs, exchange them via Keycloak's
  Standard Token Exchange V2 to an `xnat`-audience token, and provision
  the user.

Plan: see `docs/internal/xnat-auth-implementation-plan.md`.

Deploy runbook: see `docs/internal/xnat-auth-phase-a-runbook.md`.

## Build

```bash
JAVA_HOME=$(/usr/libexec/java_home -v 1.8) PATH=$JAVA_HOME/bin:$PATH \
  ./gradlew --no-daemon xnatPluginJar
```

Output: `build/libs/xnat-scout-auth-plugin-0.1.0-SNAPSHOT-xpl.jar`.

Gradle 7.5.1 needs Java 8–18. Java 22 fails with `Unsupported class
file major version 66`.

## Layout

```
src/main/java/edu/wustl/scout/xnat/auth/
├── ScoutAuthPlugin.java                 # @XnatPlugin discovery seam
├── ScoutAuthProperties.java             # Spring @Value config
├── model/ScoutIdentity.java             # normalized {sub, email, roles, groups}
├── security/
│   ├── ScoutSecurityExtension.java      # extends BaseXnatSecurityExtension
│   ├── HeaderTrustFilter.java           # OncePerRequestFilter, oauth2-proxy headers
│   ├── BearerTokenFilter.java           # OncePerRequestFilter, Phase B stub
│   └── ScoutAuthenticationToken.java
└── service/
    └── UserProvisioningService.java     # XNAT user lookup/create + role gate
```

## Configuration

Properties consumed via Spring `@Value` and read from
`xnat-conf.properties` (which the chart's `extraConfig` block populates):

| Property | Default | Purpose |
| --- | --- | --- |
| `scout.keycloak.issuer` | (empty) | Phase B: JWT issuer to validate against |
| `scout.keycloak.jwks_uri` | (empty) | Phase B: JWKS endpoint |
| `scout.keycloak.token_uri` | (empty) | Phase B: token-exchange endpoint |
| `scout.keycloak.client_id` | `xnat` | Phase B: STX target client |
| `scout.keycloak.client_secret` | (empty) | Phase B: STX client auth |
| `scout.keycloak.required_role` | `xnat-access` | Required role on validated identity |
| `scout.headers.user_header` | `X-Auth-Request-User` | oauth2-proxy preferred_username |
| `scout.headers.email_header` | `X-Auth-Request-Email` | oauth2-proxy email |
| `scout.headers.groups_header` | `X-Auth-Request-Groups` | oauth2-proxy groups |
| `scout.headers.access_token_header` | `X-Auth-Request-Access-Token` | oauth2-proxy upstream JWT |
| `scout.username_prefix` | `keycloak` | XNAT username = `<prefix>-<sub>` |
