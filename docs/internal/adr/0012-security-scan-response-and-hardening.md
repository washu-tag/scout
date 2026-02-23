# ADR 0012: Security Scan Response and Hardening

**Date**: 2026-02  
**Status**: Proposed  
**Decision Owner**: TAG Team

## Context

Two security scans were performed against Scout in February 2026 to assess the platform's external and internal security posture.

### Scans Performed

**Tenable Nessus** (2026-02-11) was run by the WashU IT department against `scout.washu.edu`. This was an external, unauthenticated scan targeting only the root domain, so requests were handled by Traefik (ingress), OAuth2 Proxy (auth middleware), and the Launchpad landing page. Internal services were not directly scanned. No critical or high severity vulnerabilities were found; 7 medium/low findings were reported.

**OWASP ZAP v2.17.0** (2026-02-16) was run internally against all service subdomains: root/Launchpad, Superset, Grafana, JupyterHub, Temporal, and Chat. Both baseline (passive) and full (active + passive) scans were performed with authenticated scanning via an OAuth2 Proxy session cookie injected through a hook script (see Appendix A). Keycloak was also tested (discovered via OAuth redirects). 25+ distinct findings were reported, including one high-risk alert (confirmed false positive).

**OWASP ZAP v2.17.0** (2026-02-20) additional baseline scans were run against two subdomains missed in the initial scan: MinIO Console (`minio.`) and Playbooks/Voilà (`playbooks.`). These are passive-only scans. Findings are consistent with the initial scan — primarily missing security headers — plus one new finding: Sub Resource Integrity (SRI) attributes missing on external CDN resources loaded by Voilà.

### Scan Coverage Comparison

| | Tenable Nessus | OWASP ZAP |
|---|---|---|
| **Type** | Commercial vulnerability scanner | Open-source web app scanner |
| **Access** | External (unauthenticated) | Internal (authenticated) |
| **Services reached** | Launchpad only (root domain) | All 8 services + Keycloak |
| **Scan depth** | Active probing (host injection, TLS) | Passive + active (spidering, fuzzing) |
| **Strengths** | TLS/SSL analysis, host-level checks | HTTP header analysis, application-layer checks, CSP analysis |
| **Blind spots** | Couldn't reach internal services | No TLS cipher testing, limited injection testing |

The two tools have complementary coverage. Tenable probes TLS configuration and host-level issues that ZAP does not test, while ZAP provides deeper HTTP header analysis and application-layer scanning across all services. Together they give a reasonably complete picture of Scout's attack surface.

### Existing Security Posture

Scout already implements several security controls, some of which were not visible to the external Tenable scan:

| Measure | Where | Notes |
|---------|-------|-------|
| TLS termination + HTTP→HTTPS redirect | Traefik | All traffic encrypted; permanent 308 redirect |
| OAuth2 Proxy authentication | All ingress routes | Keycloak-backed OIDC; role-based access (ADR 0003) |
| Content Security Policy | Open WebUI | Prevents data exfiltration via LLM-generated URLs (ADR 0009) |
| Link exfiltration filter | Open WebUI | Sanitizes external URLs in streaming responses (ADR 0010) |
| CSP frame-ancestors | JupyterHub | Prevents clickjacking via Tornado settings |
| Cookie domain restrictions | OAuth2 Proxy | Cookies scoped to deployment domain |
| Domain whitelisting | OAuth2 Proxy | `whitelist_domains` limits redirect targets |

### Consolidated Findings

Findings from both scans are merged below, organized by priority.

| Priority | Category | Finding | Services Affected | Source |
|----------|----------|---------|-------------------|--------|
| Medium | Injection | Host Header Injection | root | Tenable |
| Medium | Headers | Missing HSTS | root, grafana, jupyter, temporal, chat, minio, playbooks | Both |
| Medium | Headers | Missing X-Frame-Options / Anti-clickjacking | root, chat | Both |
| Medium | Headers | Missing CSP | root, jupyter, temporal, chat, playbooks (static routes) | Both |
| Medium | Headers | CSP quality issues (unsafe-inline, missing directives) | keycloak, superset, jupyter, temporal, chat, minio, playbooks | ZAP |
| Medium | Application | Sub Resource Integrity missing | playbooks | ZAP |
| Medium | Application | Proxy Disclosure | all services | ZAP |
| Low | Headers | Missing X-Content-Type-Options | root, grafana, jupyter, chat, playbooks | Both |
| Low | Headers | Missing Permissions Policy | root, keycloak, jupyter, temporal, chat, minio, playbooks | ZAP |
| Low | Headers | Missing Spectre isolation headers | root, keycloak, grafana, jupyter, temporal, chat, minio, playbooks | ZAP |
| Low | TLS | Weak TLS cipher suites (CBC mode) | all (Traefik-level) | Tenable |
| Low | Disclosure | Server/framework version leaks | root, jupyter, playbooks | ZAP |
| Low | Cookies | Cookie attribute issues (Secure, HttpOnly, SameSite) | keycloak, superset, grafana, jupyter, temporal, playbooks | ZAP |
| Very Low | Headers | Missing Content-Type on HTTP redirect | root | Tenable |
| Very Low | Application | Relative Path Confusion | jupyter, temporal, chat | ZAP |
| Very Low | Application | Big Redirect info leak | superset | ZAP |
| No action | Application | Source Code Disclosure - File Inclusion (false positive) | grafana | ZAP |
| No action | Application | Keycloak anti-CSRF tokens (false positive) | keycloak | ZAP |
| No action | Application | Dangerous JS functions | jupyter, minio | ZAP |
| No action | Cookies | Keycloak SameSite=None | keycloak | ZAP |
| No action | Disclosure | Timestamp disclosure | jupyter, minio, playbooks | ZAP |

The following sections detail each finding category.

---

#### Missing Security Headers

The most common findings across both scans. Most can be addressed with a single global Traefik middleware.

**Missing HSTS (Strict-Transport-Security)**
Scanners: Tenable (plugin 98056) + ZAP (10035)
Services affected: root/Launchpad, Grafana, JupyterHub, Temporal, Chat, MinIO Console, Playbooks. Superset and Keycloak already set HSTS.

Scout already redirects HTTP→HTTPS at the Traefik level (permanent 308 redirect). HSTS tells browsers to never even attempt an HTTP connection, providing defense-in-depth against SSL stripping attacks. Low actual risk given the existing redirect, but trivial to add.

**Missing X-Frame-Options / Anti-clickjacking**
Scanners: Tenable (plugin 98060) + ZAP (10020)
Services affected: root/Launchpad, Chat.

Neither `X-Frame-Options` nor CSP `frame-ancestors` is set on these services, making them theoretically vulnerable to clickjacking. Open WebUI (Chat) has CSP with `frame-src 'self'` but lacks `frame-ancestors`, which is the directive that actually prevents framing. JupyterHub already sets `frame-ancestors 'self'` via Tornado settings.

**Missing Content Security Policy**
Scanners: Tenable (plugin 112551) + ZAP (10038)
Services affected: root/Launchpad, JupyterHub, Temporal, Chat (missing entirely). Superset, Grafana, and Keycloak have CSP but with quality issues (see below).

CSP is already configured for Open WebUI (via Traefik middleware, see ADR 0009) and JupyterHub (partial, `frame-ancestors` only via Tornado settings). MinIO Console sets its own CSP (`default-src 'self' 'unsafe-eval' 'unsafe-inline'` with `script-src` and `connect-src` allowances for `https://unpkg.com`). Playbooks/Voilà sets a minimal CSP (`frame-ancestors 'self'; report-uri ...`) with no `default-src`, leaving most directives unrestricted. Launchpad and Temporal have no CSP at all. Playbooks is additionally missing CSP entirely on static routes (robots.txt, sitemap.xml).

**CSP Quality Issues**
Scanner: ZAP only (10055 family)
Services affected: Services that have CSP headers.

| Issue | Services | Notes |
|-------|----------|-------|
| Missing directives (no fallback) | keycloak, superset, jupyter, temporal, chat, minio, playbooks | `form-action` and `frame-ancestors` don't fall back to `default-src` |
| Wildcard directive | keycloak, jupyter, temporal, chat, playbooks | Overly broad source allowances |
| `script-src 'unsafe-inline'` | keycloak, jupyter, chat, playbooks | Weakens XSS protection |
| `style-src 'unsafe-inline'` | keycloak, superset, jupyter, temporal, chat, minio | Allows inline styles |
| `script-src 'unsafe-eval'` | chat, minio | Allows `eval()` calls |

The `unsafe-inline` and `unsafe-eval` directives are required by upstream applications (Keycloak, JupyterHub, Open WebUI) for their JavaScript/CSS to function. Tightening further would require nonce-based or hash-based CSP, which these applications don't support.

**Missing X-Content-Type-Options**
Scanners: Tenable (plugin 112529) + ZAP (10021)
Services affected: root/Launchpad, Grafana, JupyterHub, Chat, Playbooks (on Voilà static assets).

Prevents browsers from MIME-sniffing responses away from the declared Content-Type. Very low risk, trivial to add.

**Missing Permissions Policy**
Scanner: ZAP only (10063)
Services affected: root/Launchpad, Keycloak, JupyterHub, Temporal, Chat, MinIO Console, Playbooks.

The `Permissions-Policy` header restricts browser features (camera, microphone, geolocation, payment). Scout doesn't use any of these, so the header is purely defensive.

**Missing Spectre Isolation Headers**
Scanner: ZAP only (90004)
Services affected: root/Launchpad, Keycloak, Grafana, JupyterHub, Temporal, Chat, MinIO Console, Playbooks.

Missing `Cross-Origin-Resource-Policy`, `Cross-Origin-Embedder-Policy`, and `Cross-Origin-Opener-Policy` headers. These mitigate Spectre-class side-channel attacks. Very low actual risk — Spectre attacks require specific conditions and Scout runs on dedicated infrastructure behind authentication. `Cross-Origin-Opener-Policy` can be safely added globally, but `COEP` and `CORP` can break cross-origin OAuth flows and should not be set globally without per-service testing.

---

#### Injection and Application Issues

**Host Header Injection**
Scanner: Tenable only (plugin 98623)
Services affected: root (Traefik-level).

Traefik accepted a request with a spoofed `Host` header and returned a response. This can enable cache poisoning or password reset link manipulation if the application uses the Host header to generate URLs. Traefik IngressRoutes match on specific hostnames, but the default Traefik backend still responds to unmatched hosts. OAuth2 Proxy's `whitelist_domains` and `cookie_domains` settings limit exploitability.

**Sub Resource Integrity Attribute Missing**
Scanner: ZAP only (90003)
Services affected: Playbooks only.

Voilà loads external resources from CDN without Subresource Integrity (SRI) attributes — specifically `font-awesome@4.5.0` from `cdn.jsdelivr.net`. Without SRI, a compromised CDN could serve malicious content. This is an upstream Voilà application issue; the external stylesheet is embedded in Voilà's HTML templates. Low actual risk — all services are behind authentication and the global CSP may restrict external resource loading regardless.

**Source Code Disclosure - File Inclusion**
Scanner: ZAP only (43, High)
Services affected: Grafana only (full scan).

ZAP flagged source code disclosure via file inclusion on Grafana. This is a **confirmed false positive**. ZAP's active scanner (plugin 43) tested the `redirectTo` query parameter on `/login` by comparing the response when `redirectTo=login` (a known filename) against `redirectTo=<random string>`. The responses differed by only 74% — actually below ZAP's own 75% threshold — and the `evidence` field was empty, meaning no source code was found in either response. The `redirectTo` parameter is Grafana's standard post-login redirect path, not a file inclusion mechanism; the small response difference is due to the parameter value being reflected in a `redirectTo` cookie. Grafana is additionally behind OAuth2 Proxy authentication.

**Proxy Disclosure**
Scanner: ZAP only (40025)
Services affected: All services (full scan only).

Traefik's proxy identity is disclosed in responses. This provides minimal information to an attacker who can already determine a proxy exists from response behavior.

**Relative Path Confusion**
Scanner: ZAP only (10051)
Services affected: JupyterHub, Temporal, Chat (full scan only).

Ambiguous URL path handling could allow relative path confusion attacks. Very low actual risk — all services are behind authentication, and this primarily matters for cache poisoning on public-facing pages.

**Big Redirect Detection**
Scanner: ZAP only (10044)
Services affected: Superset only.

Superset's redirect responses are larger than expected, potentially including information in the response body. This is typical for Superset's Keycloak login redirect flow. The content is not rendered by browsers.

---

#### TLS / Transport

**Weak TLS Cipher Suites**
Scanner: Tenable only (plugin 112539)
Services affected: All (Traefik-level).

Two CBC-mode cipher suites are supported:

```
TLS1.2     TLS_ECDHE_RSA_WITH_AES_128_CBC_SHA   x25519   256
TLS1.2     TLS_ECDHE_RSA_WITH_AES_256_CBC_SHA   x25519   256
```

These are AES-CBC with ECDHE key exchange over x25519. CBC mode is less preferred than GCM but still considered safe with TLS 1.2. The key exchange is strong. ZAP does not test TLS ciphers — use `testssl.sh` for this (see Appendix A).

**Missing Content-Type on HTTP Redirect**
Scanner: Tenable only (plugin 98648)
Services affected: root (HTTP redirect response).

The HTTP→HTTPS 308 redirect response has no Content-Type header. The response has no body, so this is expected behavior.

---

#### Information Disclosure

**Server / Framework Version Leaks**
Scanner: ZAP only (10036, 10037)
Services affected: JupyterHub (`Server` header), root/Launchpad (`X-Powered-By: Next.js`), Playbooks (`Server: TornadoServer/6.4.1`).

Version information helps attackers identify known vulnerabilities, but all services are behind authentication. Very low risk.

---

#### Cookie Configuration

Scanner: ZAP only (10010, 10011, 10054)
Services affected: Various.

| Issue | Services | Cookies Affected |
|-------|----------|-----------------|
| No HttpOnly flag | keycloak, temporal, jupyter, playbooks | `KC_AUTH_SESSION_HASH`, `_csrf`, `_xsrf`, various |
| No Secure flag | superset, grafana, jupyter, playbooks | Session cookies, `oauth_state`, `redirectTo`, `_xsrf` |
| SameSite=None | keycloak | `AUTH_SESSION_ID`, `KC_AUTH_SESSION_HASH`, `KC_RESTART` |
| No SameSite attribute | jupyter, playbooks | Various, `_xsrf` |

All traffic is HTTPS-only (Traefik enforces TLS), so the missing Secure flag is mitigated at the transport level. The HttpOnly and SameSite issues are on third-party application cookies (Keycloak, JupyterHub) that we don't directly control. Keycloak's `SameSite=None` is required for cross-origin OAuth flows.

---

#### False Positives and Expected Behavior

These findings require no action:

| Finding | Scanner | Service | Why No Action Needed |
|---------|---------|---------|---------------------|
| Absence of Anti-CSRF Tokens (10202) | ZAP | Keycloak | Keycloak uses `session_code` for CSRF protection |
| Dangerous JS Functions (10110) | ZAP | JupyterHub, MinIO Console | JupyterHub executes user-provided code by design; MinIO Console uses `eval()` in bundled JavaScript |
| Keycloak SameSite=None cookies (10054) | ZAP | Keycloak | Required for cross-origin OAuth flows |
| Timestamp Disclosure (10096) | ZAP | JupyterHub, MinIO Console, Playbooks | Minimal information disclosure |
| CSP unsafe-inline/unsafe-eval (10055) | ZAP | Various | Required by upstream apps (Keycloak, JupyterHub, Open WebUI) |
| Missing Content-Type on redirect (98648) | Tenable | root | Redirect response has no body |
| Source Code Disclosure - File Inclusion (43) | ZAP | Grafana | False positive: `redirectTo` param on `/login` is a redirect path, not file inclusion; 74% similarity was below ZAP's 75% threshold; no source code in evidence |

#### Informational Findings (No Action Needed)

| ZAP ID | Finding | Services |
|--------|---------|----------|
| 10015 | Re-examine Cache-control Directives | keycloak, grafana, temporal, chat, minio, playbooks |
| 10027 | Information Disclosure - Suspicious Comments | root, jupyter, temporal, chat, minio |
| 10029 | Cookie Poisoning | grafana |
| 10049 | Non-Storable Content | all services |
| 10049 | Storable and Cacheable Content | all services |
| 10104 | User Agent Fuzzer | root, superset, grafana |
| 10109 | Modern Web Application | root, jupyter, temporal, chat, minio, playbooks |
| 10112 | Session Management Response Identified | keycloak, superset, grafana, jupyter, temporal, playbooks |
| 90027 | Cookie Slack Detector | all services |

---

## Decision

**Implement a global Traefik security headers middleware to address the majority of findings, fix application-level version disclosure in Launchpad, and accept remaining findings as false positives, upstream application limitations, or acceptable risk.**

### What We Will Fix

#### Global Traefik Security Headers Middleware

The most impactful single change. A Traefik `Middleware` resource applied to all IngressRoutes will set security headers that address the bulk of findings from both scans:

- **HSTS** (`Strict-Transport-Security`) with a one-year max-age and subdomain inclusion
- **Anti-clickjacking** (`X-Frame-Options: DENY`)
- **MIME sniffing prevention** (`X-Content-Type-Options: nosniff`)
- **Baseline Content Security Policy** restricting resource loading to `'self'` with allowances for Keycloak OAuth flows, WebSocket connections, inline styles (required by upstream apps), and `data:`/`blob:` URIs for images and fonts. The baseline includes `frame-ancestors 'none'` and `form-action` restrictions. Services with specific CSP needs (Open WebUI per ADR 0009, JupyterHub) will override via their own middleware or application-level headers.
- **Permissions Policy** disabling browser features Scout doesn't use (camera, microphone, geolocation, payment)
- **Cross-Origin-Opener-Policy** (`same-origin`) for Spectre isolation
- **Version suppression** by clearing the `Server` and `X-Powered-By` response headers

The middleware will be chained with the existing OAuth2 Proxy middlewares on all IngressRoutes, following the same pattern established in ADR 0003 and ADR 0009.

#### Playbooks Middleware Chain

The Playbooks (Voilà) IngressRoute was missing the security headers middleware. Adding `kube-system-security-headers@kubernetescrd` to the middleware chain addresses missing HSTS, X-Content-Type-Options, Permissions-Policy, Cross-Origin-Opener-Policy, and suppresses the `TornadoServer/6.4.1` Server header leak. MinIO Console already had the security headers middleware configured.

Note: The global middleware's CSP interacts with backend-set CSP headers. When both the middleware and the backend set `Content-Security-Policy`, browsers apply the most restrictive combination of all CSP headers. MinIO Console's CSP allows `https://unpkg.com` (for script-src and connect-src), and Voilà's HTML loads `font-awesome` from `cdn.jsdelivr.net` — neither CDN is in the global CSP's allowlist. If these external resources are actively used, per-service CSP overrides (following the Open WebUI pattern from ADR 0009) may be needed.

#### Application-Level Fixes

- Disable the `X-Powered-By: Next.js` header in Launchpad's Next.js configuration. While the Traefik middleware will clear this header at the ingress layer, disabling it at the source is defense-in-depth.

### What We Will Not Fix

#### Host Header Injection (Medium)

Traefik's default backend responds to requests with unrecognized `Host` headers. While this could theoretically enable cache poisoning or URL manipulation, the risk is low: OAuth2 Proxy's `whitelist_domains` and `cookie_domains` restrict exploitability, and there is no caching layer in front of Traefik. Fixing this would require configuring Traefik's default entrypoint behavior or adding host-checking middleware. We will monitor for this in future scans but will not address it immediately.

#### TLS Cipher Restriction (Low)

Two CBC-mode cipher suites are enabled at the Traefik level. These are AES-CBC with strong ECDHE-x25519 key exchange — less preferred than GCM but still safe with TLS 1.2. Restricting to GCM/ChaCha20 suites only could break older clients. We will defer this change pending client compatibility testing.

#### Cookie Attribute Issues (Low)

Missing `Secure`, `HttpOnly`, and `SameSite` attributes on various cookies are primarily set by upstream applications (Keycloak, JupyterHub, Superset, Grafana, Voilà) that we don't directly control. Voilà/Tornado sets `_xsrf` and username cookies without `Secure`, `HttpOnly`, or `SameSite` attributes. All traffic is HTTPS-only, mitigating the missing `Secure` flag at the transport level. Keycloak's `SameSite=None` is required for cross-origin OAuth flows. Cookies set by OAuth2 Proxy already have appropriate attributes.

#### Spectre Isolation — COEP and CORP (Low)

The global middleware will set `Cross-Origin-Opener-Policy: same-origin` but will intentionally omit `Cross-Origin-Embedder-Policy` and `Cross-Origin-Resource-Policy`. Setting these globally breaks cross-origin Keycloak OAuth flows across subdomains. The actual risk from Spectre attacks is very low given that Scout runs on dedicated infrastructure behind authentication.

#### Sub Resource Integrity Missing (Medium)

Voilà loads `font-awesome@4.5.0` from `cdn.jsdelivr.net` without SRI integrity attributes. This is embedded in Voilà's upstream HTML templates and cannot be fixed without patching the Voilà Docker image. The risk is low: all services are behind OAuth2 Proxy authentication, and a CDN compromise affecting a widely-used library would have broad impact beyond Scout.

#### Application-Level Findings (Very Low to No Action)

The following are accepted as false positives, by-design behavior, or very low risk:

- **Grafana Source Code Disclosure**: Confirmed false positive. ZAP's plugin 43 tested the `redirectTo` parameter on `/login` and found a 74% response similarity (below its own 75% threshold) with no actual source code in the evidence. The parameter is Grafana's post-login redirect path, not a file inclusion vector.
- **Relative Path Confusion**: Very low risk behind authentication; primarily relevant for cache poisoning on public pages.
- **Big Redirect Detection**: Normal Superset/Keycloak redirect behavior; response content is not rendered.
- **Proxy Disclosure**: Minimal value to an attacker; suppressing the `Server` header (above) reduces this signal.
- **Keycloak anti-CSRF tokens**: False positive — Keycloak uses `session_code` for CSRF protection.
- **Dangerous JS Functions**: JupyterHub is a code execution environment by design; MinIO Console uses `eval()` in its bundled JavaScript.
- **Timestamp Disclosure**: Minimal information disclosure.
- **CSP `unsafe-inline`/`unsafe-eval`**: Required by upstream applications; cannot be tightened without nonce/hash CSP support.

### Finding Disposition Summary

| Finding | Disposition |
|---------|------------|
| Missing HSTS | Fix: global middleware |
| Missing X-Frame-Options | Fix: global middleware |
| Missing CSP | Fix: global middleware (baseline) |
| Missing X-Content-Type-Options | Fix: global middleware |
| Missing Permissions Policy | Fix: global middleware |
| Missing Spectre COOP | Fix: global middleware |
| Proxy/Server/framework disclosure | Fix: global middleware |
| X-Powered-By: Next.js | Fix: Launchpad config |
| Sub Resource Integrity missing | Accept: upstream Voilà issue (CDN resources) |
| Host Header Injection | Accept: low exploitability behind OAuth2 Proxy |
| Weak TLS ciphers (CBC) | Defer: safe with TLS 1.2; needs client testing |
| Cookie attribute issues | Accept: upstream application defaults |
| Spectre COEP/CORP | Accept: breaks OAuth flows |
| CSP quality issues | Accept: upstream app requirements |
| Grafana source code disclosure | Accept: confirmed false positive |
| Relative Path Confusion | Accept: low risk behind auth |
| Big Redirect | Accept: normal Keycloak redirect behavior |
| Anti-CSRF tokens | Accept: false positive |
| Dangerous JS functions | Accept: by design |
| SameSite=None (Keycloak) | Accept: required for OAuth |
| Timestamp disclosure | Accept: minimal information |
| Missing Content-Type on redirect | Accept: no response body |

## Alternatives Considered

### Summary

| Alternative | Verdict |
|-------------|---------|
| **1. Global Traefik Middleware (Selected)** | **Selected — single point of configuration, consistent coverage** |
| 2. Per-Service Header Configuration | Rejected — duplication, inconsistency |
| 3. Application-Level Fixes Only | Rejected — incomplete coverage, upstream maintenance burden |

### Alternative 1: Global Traefik Middleware (Selected)

A single Traefik `Middleware` resource applied to all IngressRoutes, setting consistent security headers at the ingress layer.

**Pros:**
- Single point of configuration for all services
- Consistent headers regardless of backend application capabilities
- Follows existing middleware pattern (OAuth2 Proxy, Open WebUI CSP)
- Easily auditable — one resource to inspect
- New services inherit security headers automatically when added to the middleware chain

**Cons:**
- One-size-fits-all CSP may be too restrictive for some services or too permissive for others
- Services with specific needs must override via their own middleware
- Adds another middleware to the IngressRoute chain

**Verdict:** Selected. Addresses the majority of findings with minimal complexity and consistent with Scout's established middleware architecture.

### Alternative 2: Per-Service Header Configuration

Configure security headers individually in each service's Helm values or application configuration.

**Pros:**
- Each service can have precisely tailored headers
- No shared middleware dependency

**Cons:**
- Duplicated configuration across 6+ services
- Inconsistent headers when services are added or updated
- Some services (Temporal UI) have limited header configuration options
- Maintenance burden multiplies with each service

**Verdict:** Rejected. A global middleware provides consistent coverage with minimal maintenance.

### Alternative 3: Application-Level Fixes Only

Fix headers within each application (Next.js config, Grafana INI, JupyterHub Tornado settings, etc.) without a global middleware.

**Pros:**
- Headers set at the source; no infrastructure dependency
- No additional Traefik middleware in the chain

**Cons:**
- Not all services expose header configuration
- Upstream application updates may reset changes
- No coverage for services without built-in header support
- Inconsistent implementation across languages and frameworks

**Verdict:** Rejected as primary approach. Used as defense-in-depth for Launchpad's `X-Powered-By` header only.

## Consequences

### Positive

- Addresses the majority of findings from all scans with a single infrastructure change
- Consistent security headers across all current and future services
- Follows the established Traefik middleware pattern used for OAuth2 Proxy (ADR 0003) and Open WebUI CSP (ADR 0009)
- Defense-in-depth for Scout's existing TLS and authentication controls
- Verifiable: re-running ZAP scans with the rules configuration (`zap/rules.conf`) will confirm findings are resolved

### Negative

- The baseline CSP includes `'unsafe-inline'` for styles and scripts, reducing effectiveness against XSS — this is an upstream application requirement, not introduced by this change
- Services that need to embed content in frames will need to override `X-Frame-Options: DENY` via their own headers
- `Cross-Origin-Opener-Policy: same-origin` may affect services that need cross-origin popups; OAuth flows use redirects (not popups) so this should not be an issue
- Accepted findings (host header injection, TLS ciphers, cookie attributes) remain as known risks to monitor in future scans

### Operational

- ZAP scans should be re-run after deployment to verify the middleware is effective. The `zap/rules.conf` file defines expected pass/fail behavior for automated verification.
- The middleware is applied via IngressRoute annotations. New services must include `kube-system-security-headers@kubernetescrd` in their middleware chain.

## Appendix A: Running ZAP Scans

### Scan Commands

ZAP runs as a Docker container. A volume mount to `/zap/wrk` is required for working files and reports. The `-c` flag loads the rules configuration file (`zap/rules.conf`) which defines expected pass/fail behavior.

```bash
# Baseline scan (passive only, quick)
docker run -v $(pwd)/zap:/zap/wrk/:rw -t ghcr.io/zaproxy/zaproxy:stable \
  zap-baseline.py -t https://scout.example.com/ \
    -c /zap/wrk/rules.conf \
    -J report.json

# Full scan (active + passive, thorough)
docker run -v $(pwd)/zap:/zap/wrk/:rw -t ghcr.io/zaproxy/zaproxy:stable \
  zap-full-scan.py -t https://scout.example.com/ \
    -c /zap/wrk/rules.conf \
    -J report-full.json
```

Scan all service subdomains:

```bash
for sub in "" "superset." "grafana." "jupyter." "temporal." "chat." "minio." "playbooks."; do
  docker run -v $(pwd)/zap:/zap/wrk/:rw -t ghcr.io/zaproxy/zaproxy:stable \
    zap-baseline.py -t "https://${sub}scout.example.com/" \
      -c /zap/wrk/rules.conf \
      -J "report-${sub:-root}.json"
done
```

### Authenticated Scanning

Scout's multi-domain OAuth2/Keycloak flow cannot be handled by ZAP's built-in authentication. Instead, use the hook script (`zap/auth-hook.py`) to inject an OAuth2 Proxy session cookie:

1. Log into Scout via a browser
2. Copy the `_oauth2_proxy` cookie value from browser dev tools
3. Run ZAP with the hook:

```bash
export SCOUT_SESSION_COOKIE="<paste from browser dev tools>"

docker run -v $(pwd)/zap:/zap/wrk/:rw \
  -e SCOUT_SESSION_COOKIE \
  -t ghcr.io/zaproxy/zaproxy:stable \
  zap-full-scan.py \
    -t https://scout.example.com/ \
    -c /zap/wrk/rules.conf \
    -J report-full.json \
    --hook /zap/wrk/auth-hook.py
```

The cookie is valid for 8 hours (default `oauth2_proxy_cookie_expire`).

### TLS Testing

ZAP does not test TLS ciphers. For TLS and protocol testing, use `testssl.sh`:

```bash
docker run -t drwetter/testssl.sh https://scout.example.com/
```
