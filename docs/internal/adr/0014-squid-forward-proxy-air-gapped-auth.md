# ADR 0014: Squid Forward Proxy for Air-Gapped Authentication

**Date**: 2026-03
**Status**: Proposed
**Decision Owner**: TAG Team

## Context

### Current State

In air-gapped Scout deployments, the production K3s cluster has no internet access. Container images are pulled through a Harbor registry proxy on the staging node, which serves as the internet bridge (see ADR 0007).

Keycloak runs on the production cluster and handles user authentication via external identity providers (IdPs). When Microsoft or GitHub is configured as an IdP, Keycloak makes server-to-server HTTP calls to external endpoints during the OAuth2 flow:

| IdP | Endpoints | Purpose |
|-----|-----------|---------|
| **Microsoft** | `login.microsoftonline.com`, `graph.microsoft.com` | Token exchange, user info |
| **GitHub** | `github.com`, `api.github.com` | Token exchange, user info |

In an air-gapped environment, these calls fail because the production cluster cannot reach the internet. This blocks external IdP authentication entirely.

### Problem

There is no mechanism for Keycloak on the air-gapped production cluster to reach external OAuth endpoints. Harbor solves the container image problem with a pull-through proxy, but HTTP/HTTPS traffic from application pods to external APIs is not addressed.

## Decision

Install **Squid forward proxy** as a system package on the staging node and configure Keycloak to route IdP traffic through it using Keycloak's built-in `spi-connections-http-client-default-proxy-mappings` SPI.

### Architecture

```
Production Cluster (air-gapped)          Staging Node (internet access)
┌──────────────────────────┐             ┌──────────────────────────┐
│  Keycloak                │             │  Squid (port 3128)       │
│    ├─ Microsoft IdP ─────┼── proxy ──> │    ├─ login.microsoft... │──> Internet
│    └─ GitHub IdP ────────┼── proxy ──> │    └─ github.com         │──> Internet
│                          │             │                          │
│  (other services)        │             │  Harbor (registry proxy) │
│                          │             │  K3s (staging cluster)   │
└──────────────────────────┘             └──────────────────────────┘
```

### Security Model

**Domain allowlist without proxy authentication**. Squid is configured to only permit HTTPS CONNECT requests to explicitly listed domains (exact match, no subdomain wildcards). All other traffic — including plain HTTP — is denied.

Allowed domains are computed automatically from the configured IdPs by reading `keycloak_microsoft_client_id` and `keycloak_gh_client_id` from the production cluster's inventory vars:

| IdP configured | Domains allowed |
|----------------|-----------------|
| Microsoft (`keycloak_microsoft_client_id`) | `login.microsoftonline.com`, `graph.microsoft.com` |
| GitHub (`keycloak_gh_client_id`) | `github.com`, `api.github.com` |

Additional domains can be added via `squid_extra_allowed_domains` in inventory for future outbound access needs.

**Rationale for no proxy authentication**: Security relies on the combination of a strict domain allowlist and network isolation (the proxy is only reachable from the internal cluster network, not the public internet). Omitting proxy credentials simplifies the configuration and enables future use by services that may not support authenticated proxies (e.g., Open WebUI for Azure OpenAI API access).

### Deployment Model

- **Installation**: System package (`apt`/`dnf`) managed by Ansible
- **Service management**: systemd (`squid.service`)
- **Configuration**: Ansible-templated `/etc/squid/squid.conf`
- **Listening port**: 3128 (Squid default)
- **Deployment target**: Part of `make install-staging`, alongside Harbor

Squid runs as a system service rather than a Kubernetes pod because:
1. It matches how K3s itself is deployed on the staging node (system binary, not containerized)
2. Avoids Docker image channel concerns — the `ubuntu/squid` image has no stable-channel tags, only edge and beta
3. Uses the distro-maintained Squid package with standard security update channels
4. Simpler — no Helm chart, no container image management, no K8s overhead for a single-process daemon

### Keycloak Configuration

Keycloak's `spi-connections-http-client-default-proxy-mappings` SPI accepts a comma-separated list of `host-regex;proxy-url` mappings. These are injected into the Keycloak CR's `additionalOptions` conditionally:

- Only when `air_gapped: true`
- Only for IdPs that are actually configured (checked via `keycloak_microsoft_client_id` and `keycloak_gh_client_id`)

No new feature flag is introduced — the existing `air_gapped` flag and IdP client ID variables control behavior.

### Why Squid

- **Available as system package**: Included in default repositories for Rocky Linux, Ubuntu, and other major distributions
- **Battle-tested ACL system**: Squid's `dstdomain` ACLs provide explicit domain allowlisting, well-suited for a security-sensitive forward proxy
- **HTTPS CONNECT handling**: Mature support for CONNECT tunneling, which is how HTTPS traffic passes through forward proxies
- **Well-documented**: Extensive documentation and community knowledge for troubleshooting
- **BSD license**: Permissive open-source license with no commercial restrictions

### Why Not Tinyproxy

- **Regex-based filtering**: Less explicit than Squid's `dstdomain` ACLs for domain allowlisting
- **Fewer features**: No equivalent to Squid's granular ACL system for distinguishing CONNECT vs non-CONNECT traffic
- **Smaller community**: Less documentation and community support for troubleshooting

## Alternatives Considered

### Containerized Squid (Helm chart on staging K3s)

Deploy Squid as a Kubernetes pod on the staging cluster using a local Helm chart and the `ubuntu/squid` Docker image, exposed via NodePort.

**Rejected**: The `ubuntu/squid` Docker Hub image has no stable-channel tags — only `_edge` and `_beta` variants. There is no official Docker Hub `squid` image either. A system package uses the distro-maintained version with standard security update channels, avoiding container image management concerns entirely. The added complexity of a Helm chart, container image, and NodePort service is unnecessary for a single-process daemon on a node that Ansible already manages directly.

### Direct Network Route

Configure a network route from the production cluster to specific external endpoints, bypassing the staging node.

**Rejected**: Violates the air-gap security model. The staging node exists specifically to mediate all internet access. Adding direct routes undermines the architecture.

### SSH Tunnel

Use SSH port forwarding from production to staging to reach external endpoints.

**Rejected**: Fragile — SSH tunnels can drop and require monitoring/restart logic. Squid is purpose-built for proxying and handles connection pooling, timeouts, and logging natively.

### Extend Harbor for HTTP Proxying

Use Harbor or its underlying Nginx to proxy arbitrary HTTP traffic.

**Rejected**: Harbor is a container registry, not a general-purpose forward proxy. Repurposing it would be fragile and unsupported.

## Consequences

### Positive

- Enables Microsoft and GitHub IdP authentication in air-gapped Scout deployments
- Follows established staging-as-internet-bridge pattern (extends Harbor model to auth traffic)
- Configurable domain allowlist supports future outbound access needs (e.g., Azure OpenAI for Open WebUI)
- No application code changes — uses Keycloak's built-in proxy SPI
- Conditional configuration — no impact on non-air-gapped deployments
- Uses distro-maintained package with standard security updates

### Negative

- Additional system service on the staging node (minimal resource footprint)
- Port 3128 exposed on staging node (mitigated by domain allowlist and network isolation)
- Adds a network hop for IdP traffic in air-gapped deployments (negligible latency impact for OAuth flows)

## References

- [Squid Forward Proxy Documentation](http://www.squid-cache.org/Doc/)
- [Keycloak SPI: HTTP Client Proxy Mappings](https://www.keycloak.org/server/all-provider-config#_connections_http_client)
- [ADR 0007: Jump Node Architecture](0007-jump-node-architecture.md)
- [ADR 0011: Deployment Portability via Layered Architecture](0011-deployment-portability-layered-architecture.md)
