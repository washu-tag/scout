# ADR: Separation of Ansible Control Node ("Jump Node") and Staging Node

**Date**: 2025-12-03  
**Status**: Proposed  
**Decision Owner**: TAG Team

## Context

In air-gapped Scout deployments, production nodes cannot access the internet. Two supporting roles require internet access:

1. **Staging Node**: Hosts Harbor registry as a pull-through proxy for container images
2. **Ansible Control Node ("Jump Node")**: Runs Ansible playbooks that deploy and configure the production K8s cluster

This ADR addresses whether these roles should be combined on a single machine or separated.

### Initial Design

Early designs considered having the staging node also serve as the Ansible control node (jump node), since:
- It has internet access (required for both roles)
- It's already part of the infrastructure
- Simpler to manage one machine instead of two

### Security Considerations

Combining these roles concentrates risk:

```
┌─────────────────────────────────────────────────────────────────┐
│  Combined Staging + Jump Node                                   │
│                                                                 │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────────────┐   │
│  │   Harbor     │  │   Ansible    │  │  Prod K8s Admin      │   │
│  │  (serves     │  │   (scout     │  │  Credentials         │   │
│  │   images)    │  │    repo)     │  │  (kubeconfig)        │   │
│  └──────────────┘  └──────────────┘  └──────────────────────┘   │
│         +                                                       │
│  Internet Access                                                │
└─────────────────────────────────────────────────────────────────┘
```

On a combined node, any compromised Scout-installed software (Harbor, K3s, Helm) would have both:
- **Internet access**: Ability to exfiltrate data to external servers
- **Prod cluster access**: Ability to read sensitive data from production

With separation, only Helm on the jump node has both capabilities. Compromise on staging (by any means) can poison images but can't read prod data. Compromise on prod can access data but can't exfiltrate (firewall blocks egress).

**Note:** We are generally more focused on compromised software rather than external attackers. All nodes are assumed to have tight network controls (Tailscale, firewalls, SSH keys), which make direct unauthorized access unlikely; The more likely threat is a compromised upstream dependency that we install ourselves. Either way, separation reduces impact of a breach.

## Decision

**Separate the Ansible control node (jump node) from the staging node, combined with strict network controls on production nodes.**

The node separation alone is defense-in-depth. The real security boundary is the production node firewall blocking internet egress—compromised code in prod can't exfiltrate if it can't reach the internet.

### Recommended Architecture

```
┌─────────────────────────┐        ┌─────────────────────────────────┐
│  Ansible Control Node   │        │  Staging Node                   │
│  ("Jump Node")          │        │                                 │
│                         │        │  Installed: K3s, Harbor,        │
│  Installed: Helm        │        │             Traefik             │
│                         │        │                                 │
│  Also has:              │        │  Purpose: Container image       │
│  - Scout repository     │        │  caching proxy for prod         │
│  - Ansible, kubeconfig  │        │                                 │
│                         │        │  Future: LLM models,            │
│  Options:               │        │  other asset proxying           │
│  - Audited VM           │        │                                 │
│  - Developer laptop     │        │                                 │
└───────────┬─────────────┘        └──────────────┬──────────────────┘
            │                                     │
            │ K8s API (6443)                      │ HTTPS (443)
            │ SSH (22)                            │ (registry only)
            │                                     │
            ▼                                     ▼
┌──────────────────────────────────────────────────────────────────────┐
│  Production K8s Cluster                                              │
│                                                                      │
│  Installed: K3s, Scout workloads                                     │
│                                                                      │
│  Pulls images via Harbor; managed by Ansible                         │
└──────────────────────────────────────────────────────────────────────┘
```

### Security Properties

| Property | Staging | Production | Jump |
|----------|---------|------------|------|
| Internet access | Yes | No (firewall) | Yes |
| Prod cluster access | No | Yes (is the cluster) | Yes (kubeconfig) |
| Scout-installed software | K3s, Harbor, Traefik | K3s, Traefik, Scout services | Helm CLI |
| Compromise impact | Supply chain | Data (no exfil path) | **Primary risk** |

**Why jump node is the primary risk**: It's the only node with both internet access AND prod cluster credentials. Staging can poison images but prod can't exfiltrate; prod has data but can't reach internet. Jump has both.

## Consequences

### Positive

1. **Reduced attack surface**: Only jump node has both internet access and prod cluster access, and it only installs Helm vs. Harbor+K3s+Helm on a combined node
2. **Staging can't exfiltrate**: No prod cluster access, so compromised Harbor/K3s can't read or exfiltrate production data
3. **Flexibility**: Jump node can be developer laptops (with existing endpoint security) or dedicated VM

### Negative

1. **Additional infrastructure**: Two logical roles instead of one
2. **Network complexity**: Jump node needs access to both staging and prod K8s APIs
3. **Credential management**: Kubeconfig on jump nodes must be protected (file permissions, limited access)

### Limitations

This architecture does NOT protect against:
- **Compromised Jump node or Helm install**: Has both internet + prod access. See [Jump Node Hardening](#jump-node-hardening-recommended) for mitigations.
- **Browser-based exfiltration**: See [Browser-Based Exfiltration Risk](#browser-based-exfiltration-risk).
- **DNS tunneling**: If prod uses external DNS. Mitigate with internal DNS only (see [Production Node Requirements](#production-node-requirements-critical)).

## Implementation Notes

### Production Node Requirements (Critical)

These controls create the actual security boundary:

**Firewall (required):**
```
prod-nodes → staging:      ALLOW (HTTPS/443, Harbor only)
prod-nodes → internal-dns: ALLOW (DNS/53)
prod-nodes → prod-nodes:   ALLOW (cluster internal)
prod-nodes → internet:     DENY
```

**DNS (required):**
- Use internal DNS only—external DNS enables tunneling for data exfiltration
- Configure K3s with `--resolv-conf` pointing to internal resolver

### Staging Node Requirements

```
staging → internet:       ALLOW (HTTPS/443)
staging → prod-k8s-api:   DENY
staging ← prod-nodes:     ALLOW (HTTPS/443, Harbor only)
```

Also needs: Storage for Harbor cache (100GB+ recommended)

### Jump Node Requirements

```
jump → staging-k8s-api:   ALLOW (6443)
jump → prod-k8s-api:      ALLOW (6443)
jump → all-nodes:         ALLOW (SSH/22)
```

Also needs: Scout repository, Ansible installed, kubeconfig (fetched by k3s role)

### Jump Node Hardening

To reduce risk on the primary risk point:

**Helm:**
- Pin to specific version in `group_vars/all/versions.yaml`
- Verify checksum after download in helm role
- Consider running Helm in isolated container

**Ansible (user responsibility):**
- Use virtual environment with pinned dependencies
- Audit playbook changes before running

**General:**
- Prefer dedicated VM over developer laptops
- Apply endpoint security/monitoring
- Limit who has access to jump node

## Browser-Based Exfiltration Risk

**This is a known limitation of air-gapped architectures.**

If a compromised container image (e.g., Superset) contains malicious JavaScript:
1. User accesses Superset through their browser
2. Malicious JS runs in user's browser (which IS internet-connected)
3. JS can read data displayed in the UI
4. JS can exfiltrate to attacker-controlled server

The air-gap protects server-to-server exfiltration but not browser-based exfiltration.

**Mitigations:**
- Image scanning (Harbor Trivy) to detect known malicious content
- Content Security Policy headers to restrict JS network access
- Network monitoring for unusual browser traffic
- Use official images from trusted sources

## References

- [Staging Node Architecture](../staging-node-architecture.md)
- [Air-Gapped Deployment Guide](../../source/technical/air-gapped.md)
