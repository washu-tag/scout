# ADR 0012: Shared Ollama Architecture

**Date**: 2026-01-21
**Status**: Proposed
**Decision Owner**: TAG Team

## Context

Scout's optional Chat feature deploys Ollama alongside Open WebUI within each Scout cluster. The infrastructure plan calls for **shared Ollama clusters** serving multiple Scout installations:

- **PHI environments**: 3-node GPU cluster serving production and pre-production Scout
- **Non-PHI environments**: Single-node GPU cluster serving dev and demo Scout installations

This requires separating two concerns:

1. **Scout configuration**: How Scout connects to Ollama (internal or external)
2. **Ollama cluster deployment**: How to set up dedicated Ollama infrastructure

These changes build on ADR 0008 (air-gapped model distribution) and ADR 0011 (layered architecture and service-mode variables).

## Decisions

### Decision 1: Ollama Service Mode Variable

Introduce an `ollama_mode` variable to the ansible deployment following ADR 0011's service-mode pattern:

| Mode | Description |
|------|-------------|
| `disabled` | Ollama not used; chat functionality unavailable (default) |
| `deploy` | Ollama deployed as a separate service within the Scout cluster (current behavior when enabled) |
| `external` | Scout connects to an externally-provided Ollama endpoint |

When `ollama_mode: external`, the inventory must specify `ollama_url`.

**Rationale**: This pattern enables independent variation—a Scout cluster can use shared Ollama while another uses internal Ollama, without environment-specific conditionals throughout the codebase.

### Decision 2: Decouple Ollama from Open WebUI

Rather than the current deployment where Ollama is installed using an embedded configuration in the Open WebUI chart, we extract Ollama deployment into a dedicated Ansible role deployed using an Ollama-specific Helm chart. Open WebUI always connects to an Ollama endpoint via the computed `ollama_host` variable—it never embeds Ollama directly.

The embedded Ollama deployment mode (Ollama bundled inside Open WebUI's Helm chart) is explicitly not supported going forward.

**Rationale**: Clean separation allows:
- Reuse the Ollama role for deployments outside the lifecycle of a single Scout instance
- Independent management of Ollama and Open WebUI
- Simpler Open WebUI role focused solely on the chat interface, not the underlying models

### Decision 3: Ollama Cluster Owns Model Management

When deploying a shared Ollama cluster, the Ollama cluster is solely responsible for model pulls and versioning. Models are pulled during Ollama cluster installation/configuration.

Scout deployments connecting to an external Ollama (`ollama_mode: external`) will have whatever models are available without coordinating pulls. All Scout instances sharing an Ollama cluster have access to the same models.

**Consequences accepted**:
- Model updates affect all consumers simultaneously
- No version isolation between environments sharing an Ollama cluster (if isolation is needed, deploy separate Ollama clusters)

**Rationale**: Distributed model management across multiple Scout deployments introduces race conditions, version coordination complexity, and unclear ownership. Centralizing responsibility on the Ollama cluster simplifies operations.

### Decision 4: Air-Gapped Ollama Requires Pre-existing Staging

An air-gapped Ollama cluster deployment does not set up its own staging infrastructure. It requires a `staging_kubeconfig` pointing to an already-deployed staging cluster with:
- Harbor registry accessible
- NFS storage mounted

The staging cluster is set up independently—typically via Scout's `staging.yaml` playbook or a prior Scout deployment. A single staging cluster can serve both a Scout cluster and an Ollama cluster.

ADR 0008 patterns apply: staging pulls models to shared NFS; the Ollama cluster mounts NFS read-only.

**Rationale**: Reusing existing staging infrastructure avoids duplication and leverages proven air-gapped patterns. Separating staging setup from Ollama deployment clarifies responsibilities.

### Decision 5: Defer Ollama Cluster Monitoring

The shared Ollama cluster will not have a dedicated monitoring stack initially. Operators use `kubectl` and basic cluster tools for troubleshooting.

This matches the current approach for the staging node.

**Rationale**: Multi-cluster monitoring (centralized Grafana with distributed Prometheus, cross-cluster scraping, etc.) is a complex topic that warrants its own ADR. Deferring allows progress on core Ollama sharing functionality without blocking on monitoring architecture decisions.

### Decision 6: Network-Level Access Control for Shared Ollama

Ollama's API has no built-in authentication. Access control for shared Ollama clusters is enforced through **network-level firewall rules**, not application-level authentication.

Scout's authentication infrastructure (Keycloak, oauth2proxy) runs within Scout clusters and cannot practically protect an external Ollama cluster. Cross-cluster authentication adds complexity, and multiple Scout instances sharing one Ollama cluster would require arbitrating which Keycloak authorizes requests.

**Requirements** (configured externally, not by Ansible):
- Firewall rules restrict Ollama cluster access to authorized Scout cluster IPs only
- Shared Ollama is not exposed to VPN or broader internal networks
- TLS certificates must be provided for Ollama cluster ingress; cross-cluster traffic must be encrypted (certificate generation is out of scope for Scout deployment)

**MCP tools placement**: MCP tools (e.g., Trino query tool) should run on the Scout cluster where they benefit from Scout's authentication layer, not on the shared Ollama cluster.

**Rationale**: Network-level isolation is simpler and more robust than cross-cluster authentication. Keeping MCP tools on Scout prevents unauthorized PHI access if firewall rules are misconfigured.

## Consequences

### Positive

1. **Shared GPU resources**: Multiple Scout clusters share expensive GPU infrastructure
2. **Consistent patterns**: Follows ADR 0011 service-mode variable pattern
3. **Independent lifecycles**: Ollama cluster can be updated without touching Scout deployments
4. **Reusable role**: Ollama role can deploy to any cluster, not just Scout

### Negative

1. **Cross-cluster dependency**: Scout depends on external Ollama availability
2. **No model isolation**: All consumers see same model versions (accepted trade-off)
3. **Monitoring gap**: No visibility into Ollama cluster health initially
4. **External firewall dependency**: Security relies on network-level controls configured outside Ansible

## Alternatives Considered

### WashU AI API
For the TAG team's internal development use, we considered the [WashU AI API](https://it.wustl.edu/items/secure-api-access-to-ai-endpoints/). This would give us access to hosted AI models in a HIPAA-compliant environment, obviating our immediate need to share Ollama models. Though certainly not available to everyone using open-source Scout, it could have given us access to LLM APIs in our development environments without the complexity of standing up a shared Ollama cluster.

However, the WashU AI API endpoints are only available within the WashU internal network / VPN. This conflicts with the Tailscale network that our makes up our internal development environment; our development VMs cannot access the WashU AI API endpoints. 

### Cloud-Hosted Models
We could point our internal Open WebUI to a cloud-hosted model, for instance on [Azure AI Foundry](https://ai.azure.com). A POC integration we performed in a development environment did work as expected. However, our production Scout instances at WashU are air-gapped in such a way that makes it challenging to permit external traffic to a cloud model. We aren't able to rely on cloud-hosted models for our needs, so must implement another solution. 

This alternative may be viable for Scouts installations at other places with different networking configurations.

### Co-located Ollama on a Scout Cluster

Rather than deploying a dedicated Ollama cluster, we considered hosting Ollama on one Scout cluster and having other Scout clusters connect to it. This would reduce the number of clusters to manage and potentially simplify infrastructure.

However, this approach introduces significant complexity around ingress and authentication. Scout uses Traefik with oauth2-proxy middleware to protect user-facing services. To make Ollama accessible to other Scout clusters without authentication (since Ollama has no auth), we would need one of:

1. **Separate Ingress without oauth2-proxy**: Exposes Ollama to anyone who can reach the hostname; still requires external firewall rules but now the security boundary is less clear (same ingress controller serving both protected and unprotected endpoints)

2. **LoadBalancer service with firewall**: Requires MetalLB or similar for on-prem K3s, adding operational complexity; firewall rules are at the same level as the dedicated cluster approach but with more moving parts

3. **Tailscale/VPN internal routing**: Requires Tailscale deployment in each Scout cluster and coordination of ACLs, adding another networking layer

None of these options eliminate the need for network-level firewall rules—they just move where those rules apply. Meanwhile, they add complexity inside the Scout cluster by requiring careful management of which services get oauth2-proxy protection and which don't. A misconfigured Ingress annotation could inadvertently expose Ollama publicly.

The co-located approach also creates lifecycle entanglement: the Ollama service becomes tied to a specific Scout cluster's upgrade and maintenance schedule, whereas a dedicated cluster can be managed independently.

We chose the dedicated Ollama cluster approach because it provides cleaner separation of concerns with security enforced at the VM/cluster level rather than the Ingress level. The additional infrastructure is primarily the K3s control plane—the GPU resources would be dedicated regardless of which architecture we chose.

## Layer Classification (per ADR 0011)

| Layer | Scout Cluster | Ollama Cluster |
|-------|---------------|----------------|
| **Layer 0** | K3s, storage, ingress, TLS | K3s, GPU operator, ingress |
| **Layer 1** | PostgreSQL, Redis, MinIO, Keycloak | Ollama service |
| **Layer 2** | Superset, JupyterHub, Open WebUI | (none) |

Ollama as a service is Layer 1 on the Ollama cluster. When consumed externally by Scout, it appears as an external Layer 1 service.

## References

- ADR 0008: Ollama Model Distribution in Air-Gapped Environments
- ADR 0011: Deployment Portability via Layered Architecture and Service-Mode Configuration
