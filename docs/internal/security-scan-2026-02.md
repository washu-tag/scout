# Security Scan Report — February 2026

## Background

Two sets of security scans were performed against Scout in February 2026:

1. **Tenable Nessus** (2026-02-11) — Run by the WashU IT department against `scout.washu.edu`. This was an external scan targeting only the root domain, so requests were handled by Traefik (ingress), OAuth2 Proxy (auth middleware), and the Launchpad landing page. Internal services were not scanned. No critical or high severity vulnerabilities were found; 7 medium/low findings were reported.

2. **OWASP ZAP v2.17.0** (2026-02-16) — Run internally against `scout.tag.rcif.io` (same environment, moved to an internal domain). Both baseline (passive) and full (active + passive) scans were run against all service subdomains: root/Launchpad, Superset, Grafana, JupyterHub, Temporal, and Chat. Authenticated scanning was performed using an OAuth2 Proxy session cookie injected via a hook script (`zap/auth-hook.py`). Keycloak was also tested (discovered via OAuth redirects). 25+ distinct findings were reported across all services, including one high-risk alert (likely false positive).

### Scan Tool Identification

The Tenable scan was identified by:
- Nessus plugin IDs in the `definition.id` field (e.g., 98623, 112539)
- Tenable's proprietary VPR v2 scoring (`definition.vpr_v2.score`)
- The injected test string `tenablewasuxKwAWYozu.com` in the Host header injection output
- ServiceNow task ID format (`sctask0672684`) consistent with enterprise Tenable + ServiceNow integration

### Scan Coverage Comparison

| | Tenable Nessus | OWASP ZAP |
|---|---|---|
| **Type** | Commercial vulnerability scanner | Open-source web app scanner |
| **Access** | External (unauthenticated) | Internal (authenticated) |
| **Services reached** | Launchpad only (root domain) | All 6 services + Keycloak |
| **Scan depth** | Active probing (host injection, TLS) | Passive + active (spidering, fuzzing) |
| **Strengths** | TLS/SSL analysis, host-level checks | HTTP header analysis, application-layer checks, CSP quality analysis |
| **Blind spots** | Couldn't reach internal services | No TLS cipher testing, limited injection testing |

## Consolidated Findings

Findings from both scans are merged below, organized by category. Each finding notes which scanner(s) flagged it, which services are affected, and the recommended action.

### At a Glance

| Priority | Category | Finding | Services Affected | Source |
|----------|----------|---------|-------------------|--------|
| Medium | Injection | Host Header Injection | root | Tenable |
| Medium | Headers | Missing HSTS | root, grafana, jupyter, temporal, chat | Both |
| Medium | Headers | Missing X-Frame-Options / Anti-clickjacking | root, chat | Both |
| Medium | Headers | Missing CSP | root, jupyter, temporal, chat | Both |
| Medium | Headers | CSP quality issues (unsafe-inline, missing directives) | keycloak, superset, jupyter, temporal, chat | ZAP |
| Medium | Application | Proxy Disclosure | all services | ZAP |
| Low | Headers | Missing X-Content-Type-Options | root, grafana, jupyter, chat | Both |
| Low | Headers | Missing Permissions Policy | root, keycloak, jupyter, temporal, chat | ZAP |
| Low | Headers | Missing Spectre isolation headers | root, keycloak, grafana, jupyter, temporal, chat | ZAP |
| Low | TLS | Weak TLS cipher suites (CBC mode) | all (Traefik-level) | Tenable |
| Low | Disclosure | Server/framework version leaks | root, jupyter | ZAP |
| Low | Cookies | Cookie attribute issues (Secure, HttpOnly, SameSite) | keycloak, superset, grafana, jupyter, temporal | ZAP |
| Very Low | Headers | Missing Content-Type on HTTP redirect | root | Tenable |
| Very Low | Application | Relative Path Confusion | jupyter, temporal, chat | ZAP |
| Very Low | Application | Big Redirect info leak | superset | ZAP |
| Investigate | Application | Source Code Disclosure - File Inclusion | grafana | ZAP |
| No action | Application | Keycloak anti-CSRF tokens (false positive) | keycloak | ZAP |
| No action | Application | Dangerous JS functions | jupyter | ZAP |
| No action | Cookies | Keycloak SameSite=None | keycloak | ZAP |
| No action | Disclosure | Timestamp disclosure | jupyter | ZAP |

---

### Missing Security Headers

These are the most common findings across both scans. Most can be fixed with a single global Traefik middleware.

#### Missing HSTS (Strict-Transport-Security)

**Scanners**: Tenable (plugin 98056, VPR 4.8) + ZAP (10035, Low)
**Services affected**: root/Launchpad, Grafana, JupyterHub, Temporal, Chat
**Not affected**: Superset and Keycloak (already set HSTS)

Scout already redirects HTTP→HTTPS at the Traefik level (permanent 308 redirect). HSTS tells browsers to never even attempt an HTTP connection, providing defense-in-depth against SSL stripping attacks. Low actual risk, but trivial to add.

**Fix**: Add `stsSeconds: 31536000` and `stsIncludeSubdomains: true` to the global Traefik headers middleware.

#### Missing X-Frame-Options / Anti-clickjacking

**Scanners**: Tenable (plugin 98060, VPR 4.0) + ZAP (10020, Medium)
**Services affected**: root/Launchpad, Chat

Neither `X-Frame-Options` nor CSP `frame-ancestors` is set on these services, making them theoretically vulnerable to clickjacking. Open WebUI (Chat) has CSP with `frame-src 'self'` but lacks `frame-ancestors`, which is the directive that actually prevents framing. JupyterHub already sets `frame-ancestors 'self'` via Tornado settings.

**Fix**: Add `frameDeny: true` to the global Traefik headers middleware (sets `X-Frame-Options: DENY`). Services that need framing (JupyterHub) can override with their own CSP `frame-ancestors` directive.

#### Missing Content Security Policy

**Scanners**: Tenable (plugin 112551, VPR 3.8) + ZAP (10038, Medium)
**Services affected**: root/Launchpad, JupyterHub, Temporal, Chat (missing entirely)
**Not affected**: Superset, Grafana, Keycloak (have CSP, but with quality issues — see next section)

CSP is already configured for Open WebUI (via Traefik middleware, see ADR 0009) and JupyterHub (partial, `frame-ancestors` only via Tornado settings). Launchpad and Temporal have no CSP at all.

**Fix**: Add a baseline CSP to the global Traefik headers middleware. Services with specific needs (Open WebUI, JupyterHub) already override via their own middleware or application-level headers. The baseline CSP needs to include the Keycloak base URL in `connect-src` and `form-action` for OAuth flows.

#### CSP Quality Issues

**Scanner**: ZAP only (10055 family, Medium)
**Services affected**: Services that *do* have CSP headers

| Issue | Services | Notes |
|-------|----------|-------|
| Missing directives (no fallback) | keycloak, superset, jupyter, temporal, chat | `form-action` and `frame-ancestors` don't fall back to `default-src` |
| Wildcard directive | keycloak, jupyter, temporal, chat | Overly broad source allowances |
| `script-src 'unsafe-inline'` | keycloak, jupyter, chat | Weakens XSS protection |
| `style-src 'unsafe-inline'` | keycloak, superset, jupyter, temporal, chat | Allows inline styles |
| `script-src 'unsafe-eval'` | chat | Allows `eval()` calls |

The `unsafe-inline` and `unsafe-eval` directives are required by upstream applications (Keycloak, JupyterHub, Open WebUI) for their JavaScript/CSS to function. Tightening further would require nonce-based or hash-based CSP, which these applications don't support.

**Fix**: Add `frame-ancestors 'none'` and `form-action 'self'` to the baseline CSP. Accept `unsafe-inline`/`unsafe-eval` as upstream requirements. No further action practical.

#### Missing X-Content-Type-Options

**Scanners**: Tenable (plugin 112529, VPR 3.9) + ZAP (10021, Low)
**Services affected**: root/Launchpad, Grafana, JupyterHub, Chat

Prevents browsers from MIME-sniffing responses away from the declared Content-Type. Very low risk, trivial to add.

**Fix**: Add `contentTypeNosniff: true` to the global Traefik headers middleware.

#### Missing Permissions Policy

**Scanner**: ZAP only (10063, Low)
**Services affected**: root/Launchpad, Keycloak, JupyterHub, Temporal, Chat

The `Permissions-Policy` header restricts browser features (camera, microphone, geolocation, payment). Scout doesn't use any of these, so the header is purely defensive.

**Fix**: Add `Permissions-Policy: "camera=(), microphone=(), geolocation=(), payment=()"` via `customResponseHeaders` in the global Traefik headers middleware.

#### Missing Spectre Isolation Headers

**Scanner**: ZAP only (90004, Low)
**Services affected**: root/Launchpad, Keycloak, Grafana, JupyterHub, Temporal, Chat

Missing `Cross-Origin-Resource-Policy`, `Cross-Origin-Embedder-Policy`, and `Cross-Origin-Opener-Policy` headers. These mitigate Spectre-class side-channel attacks. Very low actual risk — Spectre attacks require specific conditions and Scout runs on dedicated infrastructure behind authentication.

**Fix**: Add `Cross-Origin-Opener-Policy: same-origin` to the global headers middleware. The other headers (`COEP`, `CORP`) can break cross-origin OAuth flows and should not be set globally without per-service testing.

---

### Injection and Application Issues

#### Host Header Injection

**Scanner**: Tenable only (plugin 98623, VPR 5.7)
**Services affected**: root (Traefik-level)

Traefik accepted a request with a spoofed `Host: tenablewasuxKwAWYozu.com` header and returned a response. This can enable cache poisoning or password reset link manipulation if the application uses the Host header to generate URLs. Launchpad (Next.js) uses the Host header for NextAuth callbacks. OAuth2 Proxy's `whitelist_domains` and `cookie_domains` settings limit exploitability.

Traefik IngressRoutes match on `host: {{ server_hostname }}`, but the default Traefik backend still responds to unmatched hosts.

**Fix**: Configure Traefik to return 404/503 for unmatched Host headers by setting the default entrypoint behavior, or add explicit host-checking middleware.

**Effort**: ~1-2 hours.

#### Source Code Disclosure - File Inclusion (Investigate)

**Scanner**: ZAP only (43, High)
**Services affected**: Grafana only (full scan)

ZAP flagged what appears to be source code disclosure via file inclusion on Grafana. This is likely a **false positive** triggered by Grafana's API responses that return configuration or dashboard JSON containing code-like content (Go template syntax, panel queries, etc.).

**Actual risk**: Low — Grafana is behind OAuth2 Proxy authentication, and its API responses are intentional. However, the High risk rating warrants investigation.

**Fix**: Investigate the specific URL to confirm this is Grafana API behavior rather than an actual file inclusion vulnerability. If confirmed as a false positive, no action needed.

**Effort**: ~1 hour to investigate.

#### Proxy Disclosure

**Scanner**: ZAP only (40025, Medium)
**Services affected**: All services (full scan only)

Traefik's proxy identity is disclosed in responses. This provides minimal information to an attacker who can already determine a proxy exists from response behavior.

**Fix**: Optionally suppress the `Server` header via the global Traefik headers middleware. Low priority — security through obscurity.

#### Relative Path Confusion

**Scanner**: ZAP only (10051, Medium)
**Services affected**: JupyterHub, Temporal, Chat (full scan only)

Ambiguous URL path handling could allow relative path confusion attacks. Very low actual risk — all services are behind authentication, and this primarily matters for cache poisoning on public-facing pages.

**Fix**: No immediate action needed.

#### Big Redirect Detection

**Scanner**: ZAP only (10044, Low)
**Services affected**: Superset only

Superset's redirect responses are larger than expected, potentially including information in the response body. This is typical for Superset's Keycloak login redirect flow. The content is not rendered by browsers.

**Fix**: No action needed.

---

### TLS / Transport

#### Weak TLS Cipher Suites

**Scanner**: Tenable only (plugin 112539, VPR 3.9)
**Services affected**: All (Traefik-level)

Two CBC-mode cipher suites are supported:

```
TLS1.2     TLS_ECDHE_RSA_WITH_AES_128_CBC_SHA   x25519   256
TLS1.2     TLS_ECDHE_RSA_WITH_AES_256_CBC_SHA   x25519   256
```

The scanner's description generically mentions RC4 and 3DES, but the actual ciphers found are AES-CBC with ECDHE key exchange over x25519. These are CBC mode (less preferred than GCM) but still considered safe with TLS 1.2. The key exchange is strong. ZAP does not test TLS ciphers — use `testssl.sh` for this.

**Fix**: Create a Traefik `TLSOption` resource restricting ciphers to GCM and ChaCha20 suites:

```yaml
apiVersion: traefik.io/v1alpha1
kind: TLSOption
metadata:
  name: default
  namespace: kube-system
spec:
  minVersion: VersionTLS12
  cipherSuites:
    - TLS_ECDHE_RSA_WITH_AES_128_GCM_SHA256
    - TLS_ECDHE_RSA_WITH_AES_256_GCM_SHA384
    - TLS_ECDHE_RSA_WITH_CHACHA20_POLY1305_SHA256
```

**Caveat**: Disabling CBC ciphers could break older clients. Test before deploying.

**Effort**: ~1 hour.

#### Missing Content-Type on HTTP Redirect

**Scanner**: Tenable only (plugin 98648, VPR 3.9)
**Services affected**: root (HTTP redirect response)

An invalid/missing Content-Type header on `http://scout.washu.edu/`. This is the HTTP→HTTPS 308 redirect response generated by Traefik, which has no body and therefore no meaningful Content-Type.

**Fix**: No action needed. False positive on a redirect response.

---

### Information Disclosure

#### Server / Framework Version Leaks

**Scanner**: ZAP only (10036 Low, 10037 Low)
**Services affected**: JupyterHub (Server header), root/Launchpad (X-Powered-By: Next.js)

Version information helps attackers identify known vulnerabilities, but all services are behind authentication. Very low risk.

**Fix**: Set `poweredByHeader: false` in Launchpad's `next.config.ts`. Suppress `Server` header via Traefik middleware (`Server: ""`). Low priority.

**Effort**: ~30 minutes.

---

### Cookie Configuration

**Scanner**: ZAP only (10010, 10011, 10054 — all Low)
**Services affected**: Various

| Issue | Services | Cookies Affected |
|-------|----------|-----------------|
| No HttpOnly flag | keycloak, temporal, jupyter | `KC_AUTH_SESSION_HASH`, `_csrf`, various |
| No Secure flag | superset, grafana, jupyter | Session cookies, `oauth_state`, `redirectTo` |
| SameSite=None | keycloak | `AUTH_SESSION_ID`, `KC_AUTH_SESSION_HASH`, `KC_RESTART` |
| No SameSite attribute | jupyter | Various |

All traffic is HTTPS-only (Traefik enforces TLS), so the missing Secure flag is mitigated at the transport level. The HttpOnly and SameSite issues are mostly on third-party application cookies (Keycloak, JupyterHub) that we don't directly control. Keycloak's `SameSite=None` is required for cross-origin OAuth flows.

**Fix**: For cookies set by Traefik or OAuth2 Proxy, ensure Secure and HttpOnly flags are set (OAuth2 Proxy already does this). For upstream application cookies, these are application defaults that we don't control. Low priority.

**Effort**: ~1 hour to audit.

---

### False Positives and Expected Behavior

These findings require no action:

| Finding | Scanner | Service | Why No Action Needed |
|---------|---------|---------|---------------------|
| Absence of Anti-CSRF Tokens (10202) | ZAP | Keycloak | False positive — Keycloak uses `session_code` for CSRF protection |
| Dangerous JS Functions (10110) | ZAP | JupyterHub | JupyterHub executes user-provided code by design |
| Keycloak SameSite=None cookies (10054) | ZAP | Keycloak | Required for cross-origin OAuth flows |
| Timestamp Disclosure (10096) | ZAP | JupyterHub | Minimal information disclosure |
| CSP unsafe-inline/unsafe-eval (10055) | ZAP | Various | Required by upstream apps (Keycloak, JupyterHub, Open WebUI) |
| Missing Content-Type on redirect (98648) | Tenable | root | Redirect response has no body |

### Informational Findings (No Action Needed)

| ZAP ID | Finding | Services |
|--------|---------|----------|
| 10015 | Re-examine Cache-control Directives | keycloak, grafana, temporal, chat |
| 10027 | Information Disclosure - Suspicious Comments | root, jupyter, temporal, chat |
| 10029 | Cookie Poisoning | grafana |
| 10049 | Non-Storable Content | all services |
| 10049 | Storable and Cacheable Content | all services |
| 10104 | User Agent Fuzzer | root, superset, grafana |
| 10109 | Modern Web Application | root, jupyter, temporal, chat |
| 10112 | Session Management Response Identified | keycloak, superset, grafana, jupyter, temporal |
| 90027 | Cookie Slack Detector | all services |

---

## Existing Security Posture

Scout already implements several security measures, some of which were not visible to the external Tenable scan:

| Measure | Where | Notes |
|---------|-------|-------|
| TLS termination + HTTP→HTTPS redirect | Traefik | All traffic encrypted; permanent 308 redirect |
| OAuth2 Proxy authentication | All ingress routes | Keycloak-backed OIDC; role-based access |
| Content Security Policy | Open WebUI | Prevents data exfiltration via LLM-generated URLs (ADR 0009) |
| Link exfiltration filter | Open WebUI | Sanitizes external URLs in streaming responses (ADR 0010) |
| CSP frame-ancestors | JupyterHub | Prevents clickjacking via Tornado settings |
| Cookie domain restrictions | OAuth2 Proxy | Cookies scoped to `.{{ server_hostname }}` |
| Domain whitelisting | OAuth2 Proxy | `whitelist_domains` limits redirect targets |

## Recommended Fix Strategy

### Step 1: Global Traefik Security Headers Middleware

The most impactful single fix. A global Traefik headers middleware applied to all routes addresses the majority of findings from both scans:

```yaml
apiVersion: traefik.io/v1alpha1
kind: Middleware
metadata:
  name: security-headers
  namespace: kube-system
spec:
  headers:
    # HSTS (Tenable 98056, ZAP 10035)
    stsSeconds: 31536000
    stsIncludeSubdomains: true
    # X-Frame-Options: DENY (Tenable 98060, ZAP 10020)
    frameDeny: true
    # X-Content-Type-Options: nosniff (Tenable 112529, ZAP 10021)
    contentTypeNosniff: true
    # Content Security Policy (Tenable 112551, ZAP 10038)
    # Baseline policy; services with specific needs (Open WebUI, JupyterHub)
    # override via their own middleware or application-level headers
    contentSecurityPolicy: >-
      default-src 'self';
      script-src 'self';
      style-src 'self' 'unsafe-inline';
      img-src 'self' data:;
      connect-src 'self' {{ keycloak_base_url }};
      form-action 'self' {{ keycloak_base_url }};
      frame-ancestors 'none'
    customResponseHeaders:
      # Permissions Policy (ZAP 10063)
      Permissions-Policy: "camera=(), microphone=(), geolocation=(), payment=()"
      # Spectre isolation (ZAP 90004)
      Cross-Origin-Opener-Policy: "same-origin"
      # Suppress version/framework disclosure (ZAP 10036, 10037, 40025)
      X-Powered-By: ""
      Server: ""
```

Add `kube-system-security-headers@kubernetescrd` to the middleware chain for all IngressRoutes (Launchpad, Superset, Grafana, Temporal, JupyterHub, Open WebUI).

Additionally, set `poweredByHeader: false` in Launchpad's `next.config.ts`.

**Findings resolved**: HSTS, X-Frame-Options, X-Content-Type-Options, CSP (baseline), Permissions Policy, Spectre COOP, Proxy Disclosure, Server/framework leaks.

**Effort**: ~2-3 hours including testing.

### Step 2: Host Header Validation

Configure Traefik to reject requests with unrecognized Host headers.

**Findings resolved**: Host Header Injection.

**Effort**: ~1-2 hours.

### Step 3: TLS Cipher Restriction (Optional)

Create a Traefik `TLSOption` to restrict cipher suites to GCM/ChaCha20 only. Test with clients before deploying.

**Findings resolved**: Weak TLS cipher suites.

**Effort**: ~1 hour.

### Step 4: Investigate Grafana Source Code Disclosure

Confirm ZAP's high-risk finding (plugin 43) is a false positive from Grafana's API responses.

**Effort**: ~1 hour.

### Step 5: Cookie Attribute Audit

Review Secure/HttpOnly/SameSite on cookies set by services we control (OAuth2 Proxy, Traefik). Upstream application cookies (Keycloak, JupyterHub, Superset) are not directly configurable.

**Effort**: ~1 hour.

### Summary

| Step | Effort | Findings Resolved |
|------|--------|-------------------|
| Global security headers middleware | ~2-3 hours | ~15 findings across both scans |
| Host header validation | ~1-2 hours | 1 (highest actual risk) |
| TLS cipher restriction | ~1 hour | 1 (optional) |
| Investigate Grafana disclosure | ~1 hour | 1 (confirm false positive) |
| Cookie attribute audit | ~1 hour | ~4 (where controllable) |
| **Total** | **~1.5 days** | |

---

## Running Your Own Scans

### Tools

| Tool | Type | What it covers | Cost |
|------|------|----------------|------|
| **OWASP ZAP** | Web application scanner | Headers, XSS, injection, auth issues | Free / open source |
| **testssl.sh** | TLS/SSL scanner | Cipher suites, protocol versions, certificate issues | Free / open source |
| **Nikto** | Web server scanner | Missing headers, server misconfigs | Free / open source |
| **nuclei** | Template-based scanner | Broad vulnerability detection with community templates | Free / open source |
| **Mozilla Observatory** | Online header checker | Security headers audit | Free (online service) |

### Running ZAP

ZAP runs as a Docker container. A volume mount to `/zap/wrk` is required for working files and reports:

```bash
# Baseline scan (passive only, quick)
docker run -v $(pwd)/zap:/zap/wrk/:rw -t ghcr.io/zaproxy/zaproxy:stable \
  zap-baseline.py -t https://scout.example.com/ -J report.json

# Full scan (active + passive, more thorough)
docker run -v $(pwd)/zap:/zap/wrk/:rw -t ghcr.io/zaproxy/zaproxy:stable \
  zap-full-scan.py -t https://scout.example.com/ -J report-full.json
```

Scan all service subdomains:

```bash
for sub in "" "superset." "grafana." "jupyter." "temporal." "chat."; do
  docker run -v $(pwd)/zap:/zap/wrk/:rw -t ghcr.io/zaproxy/zaproxy:stable \
    zap-baseline.py -t "https://${sub}scout.example.com/" -J "report-${sub:-root}.json"
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
    -J report-full.json \
    --hook /zap/wrk/auth-hook.py
```

The cookie is valid for 8 hours (default `oauth2_proxy_cookie_expire`).

### Running testssl.sh

For TLS cipher and protocol testing (not covered by ZAP):

```bash
docker run -t drwetter/testssl.sh https://scout.example.com/
```

### Recommended Workflow

1. Run ZAP baseline + full scans against all service subdomains after deploying fixes
2. Run `testssl.sh` to verify cipher suite changes
3. Consider adding ZAP baseline to CI/CD or periodic checks
