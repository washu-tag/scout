# scout-xnat-auth-plugin

> **Status: checked in for review.** This plugin is the output of a
> POC that explored how XNAT could be integrated into Scout's auth
> posture. Scout doesn't deploy XNAT in `main` yet, so nothing in the
> deployed cluster loads this plugin today. The code and tests are
> in-tree so reviewers can read it, comment on it, and so the work
> isn't lost while the broader integration decision is pending. The
> matching Keycloak realm changes, the `xnat-values.yaml` helm values
> that install the plugin, and the deployment runbooks all live on
> the original POC branch (`xnat-dev-wt`).

XNAT plugin that integrates XNAT into Scout's auth posture:

- **`HeaderTrustFilter`** — trusts a single forwarded access token
  (`X-Auth-Request-Access-Token`, set by oauth2-proxy) for browser-path
  traffic. The token is the sole source of identity (`sub`,
  `preferred_username`, `email`, names) and the required-role gate; it
  fails closed (401 on an unparseable token, 403 when the token lacks
  the required role). Provisions the matching XNAT user on first sight
  via `UserProvisioningService`.
- **`BearerTokenFilter`** — validates Keycloak JWTs from
  `Authorization: Bearer …` directly by audience: signature/issuer/expiry,
  then `aud` must contain the `xnat` client and the token must carry the
  required role. The audience is the confinement boundary on the API path
  (there's no network trust boundary, so the token itself must be scoped to
  xnat — Keycloak emits `aud=xnat` only for clients with the `xnat-audience`
  mapper, eg. jupyterhub). Provisions the user. No server-side token exchange.

## Build

```bash
JAVA_HOME=$(/usr/libexec/java_home -v 1.8) PATH=$JAVA_HOME/bin:$PATH \
  ./gradlew --no-daemon xnatPluginJar
```

Output: `build/libs/scout-xnat-auth-plugin-0.1.0-SNAPSHOT-xpl.jar`.

Gradle 8.14.4 (pinned in `gradle/wrapper/gradle-wrapper.properties`) runs on
Java 8–24. We stay on Gradle 8.x on purpose — see the note at the top of
`build.gradle`.

## Layout

```
src/main/java/edu/wustl/scout/xnat/auth/
├── ScoutAuthPlugin.java                 # @XnatPlugin discovery seam
├── ScoutAuthConfig.java                 # @Configuration; @Beans for filters + services
├── ScoutAuthProperties.java             # Spring @Value config
├── model/ScoutIdentity.java             # normalized {sub, preferred_username, email, names, roles}
├── security/
│   ├── ScoutSecurityExtension.java      # extends BaseXnatSecurityExtension
│   ├── HeaderTrustFilter.java           # OncePerRequestFilter, oauth2-proxy headers
│   ├── BearerTokenFilter.java           # OncePerRequestFilter, JWT + audience check + provision
│   ├── JwtValidator.java                # Nimbus-backed JWT/JWKS validator
│   └── ScoutAuthenticationToken.java
└── service/
    └── UserProvisioningService.java     # XNAT user lookup/create + role gate
```

## Configuration

Properties consumed via Spring `@Value` and read from
`xnat-conf.properties` (which the chart's `extraConfig` block populates):

| Property | Default | Purpose |
| --- | --- | --- |
| `scout.keycloak.issuer` | (empty) | JWT issuer to validate against |
| `scout.keycloak.jwks_uri` | (empty) | JWKS endpoint |
| `scout.keycloak.client_id` | `xnat` | Expected `aud` on bearer tokens; client whose roles are read |
| `scout.keycloak.required_role` | `xnat-access` | Required role on validated identity |
| `scout.headers.access_token_header` | `X-Auth-Request-Access-Token` | oauth2-proxy upstream JWT (sole browser-path identity source) |
| `scout.username_prefix` | `keycloak` | XNAT username = `<prefix>-<sub>` |
