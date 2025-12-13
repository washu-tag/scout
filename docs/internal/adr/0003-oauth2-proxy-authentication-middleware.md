# ADR 0003: OAuth2 Proxy as Authentication Middleware

**Date**: 2025-10-29  
**Status**: Accepted  
**Decision Owner**: TAG Team

## Context

All Scout services must be protected by authentication and authorization, but the services have varying levels of built-in authentication support:

- **Services with OAuth/OIDC support:** Superset, JupyterHub, Grafana, Temporal UI, MinIO Console, Launchpad
- **Services with basic/no auth:** Potential future services like Ollama, Prometheus, or custom tools

Key requirements:
1. **User approval workflow:** New users must be approved by administrators before accessing ANY Scout service
2. **Single Sign-On (SSO):** Users should authenticate once and access all services
3. **Unified user experience:** Consistent login flow and pending approval experience across services
4. **Service flexibility:** Ability to add services with or without built-in authentication

### User Approval Challenge

Scout implements a security requirement where new users who authenticate via the institutional identity provider through Keycloak cannot immediately access services. Instead:

1. User authenticates successfully with their institutional identity provider via Keycloak
2. Keycloak creates the user account
3. User has NO roles assigned (pending state)
4. Administrator receives email notification via Keycloak event listener
5. Administrator manually assigns user to `scout-user` group
6. User receives approval email and can now access services

**Critical question:** How do we enforce this approval check across all services and give a consistent user experience?

## Decision

**Use a hybrid authentication approach combining OAuth2 Proxy middleware with direct service OAuth/OIDC integration.**

Scout implements a two-layer authentication architecture:

**Layer 1: OAuth2 Proxy (Centralized Approval Gateway)**
- Validates user authentication via Keycloak OIDC
- Enforces the user approval requirement by checking for the `oauth2-proxy-user` client role (inherited from `scout-user` group)
- Redirects unapproved users to a "Pending Approval" page
- Provides consistent login flow across all services

**Layer 2: Direct Service Integration (Service-Specific Authorization)(Optional)**
- Each service implements its own OAuth/OIDC client with Keycloak
- Services retrieve their own client-specific roles for authorization
- Services maintain their own session cookies

This separation of concerns means:
- OAuth2 Proxy handles user approval (oauth2-proxy-user client role check)
- Services handle their own authorization (service-specific client roles if service supports OAuth/OIDC)

### Architecture

```
User Request
    ↓
Traefik Ingress
    ↓
OAuth2 Proxy Middleware (ForwardAuth)
    ├─ Check authentication (oauth2_proxy session cookie)
    ├─ Check authorization (oauth2-proxy-user client role from Keycloak)
    ├─ Redirect to Keycloak if unauthenticated
    ├─ Show "Pending" page if unauthorized
    └─ Forward to service if approved
        ↓
    Service (Superset, JupyterHub, Grafana, etc.)
        ├─ OAuth/OIDC authentication with Keycloak
        ├─ Retrieves service-specific client roles
        └─ Maintains own session cookie
```

## Alternatives Considered

### Summary

| Alternative | Verdict |
|-------------|---------|
| **1. Hybrid: OAuth2 Proxy + Direct Service Integration (Selected)** | **Selected - Best separation of concerns** |
| 2. OAuth2 Proxy Only | Considered - Services still need client roles for authZ |
| 3. Direct Service Integration Only | Rejected - Mixed concerns, poor UX |
| 4. API Gateway (Kong/Ambassador) | Dismissed - too heavy/complex for needs |
| 5. Service Mesh (Istio/Linkerd) | Dismissed - too heavy/complex, missing capability |

### Alternative 1: Hybrid Approach (Selected)

Use OAuth2 Proxy for user approval AND direct service OAuth/OIDC integration for service-specific authorization.

**Pros:**
- Clean separation of concerns: OAuth2 Proxy handles approval, services handle authorization
- Services focus on their own client roles without approval logic
- Centralized user approval enforcement at ingress layer
- Unified "Pending Approval" page across all services
- Services can retrieve client-specific roles from Keycloak
- Protects services with or without OAuth/OIDC support
- Well-documented OAuth2 Proxy project

**Cons:**
- Two authentication components to maintain (OAuth2 Proxy + per-service clients)
- Multiple session cookies (oauth2_proxy + service-specific)

**Verdict:** Selected for best separation of concerns and maintainability.

### Alternative 2: OAuth2 Proxy Only

Use only OAuth2 Proxy with header- and token-based authentication for services.

**Pros:**
- Single authentication layer
- One session cookie for all services
- Simpler architecture

**Cons:**
- Not all services support header- and token-based auth
- Services need to easily retrieve their own client-specific roles from Keycloak and it's easier with OAuth/OIDC clients

**Verdict:** Considered but rejected because some Scout services don't support header-based or token-based auth and need their own OAuth/OIDC clients anyway.

### Alternative 3: Direct Service Integration Only

Each service implements OAuth/OIDC directly with Keycloak. No centralized middleware.

**Investigation:** Fully evaluated as the primary alternative to OAuth2 Proxy.

**Pros:**
- Fewer moving parts (no OAuth2 Proxy deployment)
- Services have direct Keycloak integration

**Cons:**
- **Mixed authorization concerns:** Services would handle both user approval logic AND service-specific authorization
- **Cannot protect services without OAuth/OIDC support:** Tools like Prometheus, Ollama cannot be added. Future playbook services would need OAuth/OIDC support built-in.
- **Configuration duplication:** Every service needs Keycloak client configuration
- **No unified "Pending" page:** Each service shows different error when user lacks approval
- **Development burden:** Every new service requires OAuth/OIDC implementation and approval logic testing

**Verdict:** Rejected in favor of hybrid approach that provides better separation of concerns and unified user experience.

### Alternative 4: API Gateway (Kong, Ambassador, Tyk)

**Verdict:** Dismissed - too heavy and complex for Scout's needs. Would introduce substantial infrastructure (database, admin UI, plugins) and overlap with existing Traefik functionality.

### Alternative 5: Service Mesh (Istio, Linkerd)

**Verdict:** Dismissed - Istio only validates existing tokens, doesn't handle browser-based OAuth2 redirects (would still need OAuth2 Proxy anyway). Very heavy infrastructure with high operational complexity. Mixing Traefik and Istio would create configuration complexity.

## Rationale

The hybrid approach was selected for three core reasons:

**1. Separation of Concerns**

OAuth2 Proxy handles user approval (`oauth2-proxy-user` client role check) while services handle their own authorization (client-specific roles like `superset-admin`, `grafana-editor`). Without OAuth2 Proxy, services would need to check for oauth2-proxy-user role in addition to their own client roles, mixing approval workflow with service authorization logic.

**2. Centralized Approval Enforcement with Unified UX**

OAuth2 Proxy checks for the `oauth2-proxy-user` client role at the ingress layer before any service receives the request. This provides:
- Consistent "Pending Approval" page across all services
- Single source of truth for user approval status
- Simpler service implementation (no approval logic needed)
- Unified login flow

**3. Service Flexibility**

OAuth2 Proxy can protect any service regardless of authentication capabilities:
- Services with OAuth/OIDC support: Automatically get approval enforcement layer
- Services without auth (Prometheus, Ollama): Fully protected by OAuth2 Proxy alone
- Future services: Inherit approval workflow without additional code

## Consequences

### Positive

- Centralized user approval enforcement
- Clean separation of concerns (approval vs authorization)
- Unified user experience with consistent "Pending Approval" page
- Simplified service development - no approval logic needed

### Negative

- Additional component to maintain (OAuth2 Proxy)
- Single point of failure if OAuth2 Proxy is down
- Multiple session cookies (oauth2_proxy + per-service cookies)

## Future Considerations

Potential enhancements include Redis session storage if cookie size becomes an issue, and rate limiting for brute force protection.
