# ADR 0012: Deployment Portability via Layered Architecture and Service-Mode Configuration

**Date**: 2026-01  
**Status**: Proposed  
**Decision Owner**: TAG Team

## Context

Scout's deployment is currently a single Ansible codebase that mixes three concerns:

1. **Infrastructure provisioning** — Installing K3s, configuring storage, setting up TLS
2. **Platform services** — Deploying database operators, object storage, authentication
3. **Applications** — Installing Superset, JupyterHub, Grafana, Temporal

This worked for the original on-premise environment. Our effort to deploy to AWS revealed assumptions embedded in the Ansible:

- K3s is installed (not applicable to EKS)
- CloudNativePG provides PostgreSQL (AWS uses RDS)
- Storage uses hostPath volumes (AWS uses EBS/EFS)
- Traefik handles ingress (AWS uses ALB)

Resolving each difference requires either adding conditionals or removing components entirely. The playbooks grow more complex, and the "one inventory file to deploy everything" approach strains under environment-specific variations.

### Scout is a Platform

Scout is a **platform**, not an application. It deploys Kubernetes operators, databases, workflow engines, analytics tools, and monitoring infrastructure. This creates inherent deployment complexity that must be managed somewhere. The question is not "how do we eliminate it?" but "how do we organize it?"

### Key Technical Challenges

Any approach to deployment portability must address:

1. **Service dependency ordering**: Scout deploys 6+ Kubernetes operators and their Custom Resources with complex dependency chains (e.g., cert-manager → Cassandra Operator → CassandraDatacenter CR). Most Kubernetes tools (Helm, Kustomize) apply all resources in a single transaction without sequencing.
2. **Operator-before-CRD timing**: Operators must be fully running before their Custom Resources can be applied. Helm's `crds/` directory doesn't wait for operator webhooks to be ready.
3. **Non-standard readiness checks**: Several Scout dependencies use non-standard status fields (e.g., Elasticsearch `status.health=green`, MinIO StatefulSet replicas) that Helm's `--wait` doesn't understand.
4. **Database initialization sequencing**: Multiple services require databases to exist in PostgreSQL before they can start, but database creation requires PostgreSQL to be running first—a chicken-and-egg problem requiring careful orchestration.
5. **Cross-service configuration**: Services need connection information from other services (Hive needs PostgreSQL and MinIO credentials, Superset needs PostgreSQL, Redis, and Trino details, etc.).
6. **Platform-specific storage**: Different platforms use different storage patterns (local-path dynamic volumes on-prem, EBS/EFS on AWS, Persistent Disk on GCP).
7. **Ingress controller portability**: Scout uses Traefik-specific Middleware CRDs that don't work with nginx, AWS ALB, or other ingress controllers.

### Why Ansible Remains Our Orchestration Tool

We evaluated nine deployment approaches: Helm umbrella charts, Helmfile, ArgoCD, Flux CD, Carvel (kapp-controller), Crossplane, Pulumi, Timoni, and OLM.

**Key finding**: Helm alone is insufficient for Scout's complexity. The operator dependency chains and database initialization requirements cannot be expressed in a single Helm release. Tools that solve sequencing (ArgoCD, Flux, Helmfile) either require GitOps infrastructure or provide unclear benefits over what we already have.

Scout's deployment has characteristics that favor Ansible's orchestration model:

- **Operator → CRD → CR chains** require explicit waiting that Ansible handles naturally
- **Database initialization** has chicken-and-egg problems that need imperative sequencing
- **Complex conditionals** — environment-specific logic is easier to express in Ansible

Rather than replace the tool, we can improve portability by restructuring how we use it. This ADR addresses two questions:

1. **How do we reason about what varies where?** → Decision 1: Layered Model
2. **How do we handle that variation in code?** → Decision 2: Service-Mode Variables

## Decision 1: Adopt a Layered Deployment Model

We adopt a three-layer model for reasoning about Scout deployment:

| Layer | What it contains | How it varies |
|-------|------------------|---------------|
| **Layer 0: Infrastructure** | Kubernetes cluster, storage classes, ingress controller, TLS certificates, node configuration | Fundamentally different per environment |
| **Layer 1: Platform Services** | PostgreSQL, Redis, MinIO/S3, Keycloak, cert-manager | May be deployed by Scout or provided externally |
| **Layer 2: Applications** | Superset, JupyterHub, Grafana, Temporal, extractors | Core Scout functionality; aim for consistency |

Different environments enter at different layers:

| Environment | Layer 0 | Layer 1 | Layer 2 |
|-------------|---------|---------|---------|
| On-prem (bare metal) | Ansible installs K3s | Ansible deploys operators | Ansible deploys apps |
| AWS (full Scout) | Terraform creates EKS | Terraform creates RDS, S3 | Ansible deploys apps |
| AWS (BYO services) | Terraform creates EKS | User provides database/storage | Ansible deploys apps |
| Existing cluster | Provided | Varies | Ansible deploys apps |

**The goal is not uniformity across all layers, but consistency where it matters.** Layer 2 should be as similar as possible across environments, though some variation is expected:

- A site with existing Superset may only need Scout's dashboards deployed
- Extractors will vary between installations based on data sources
- Optional components (Chat, GPU support) may be enabled or disabled

The value of this model is that it clarifies which variations are expected (Layer 0-1) versus which represent unnecessary divergence (Layer 2).

### Alternatives Considered for Decision 1

#### Alternative 1a: Continue with Monolithic Approach

**Description**: No explicit layers; continue mixing infrastructure, platform, and application concerns throughout the Ansible codebase.

**Advantages**:
- No restructuring effort required
- Single mental model (everything is "Scout deployment")

**Disadvantages**:
- Unclear what *should* vary between environments versus what's accidental complexity
- Conditionals scattered throughout; hard to reason about
- New platforms require understanding the entire codebase

**Decision**: Reject. The portability problems we're experiencing stem from this approach.

#### Alternative 1b: Two-Layer Model (Infrastructure vs Application)

**Description**: Split into just two layers: infrastructure (everything needed to run Scout) and application (Scout itself).

**Advantages**:
- Simpler than three layers
- Clear division of responsibility

**Disadvantages**:
- Doesn't capture the reality that platform services (PostgreSQL, Redis, S3) can be either deployed by Scout or provided externally
- Forces a binary choice: either Scout deploys everything, or nothing
- "Infrastructure" layer becomes bloated with optional components

**Decision**: Reject. The three-layer model better captures real-world deployment patterns.

#### Alternative 1c: Per-Environment Playbooks

**Description**: Fork the Ansible into separate codebases or playbook sets for each environment (on-prem, AWS, etc.).

**Advantages**:
- Each environment is fully self-contained
- No conditional logic needed within an environment

**Disadvantages**:
- Maintenance burden multiplies with each environment
- Drift between environments over time
- Bug fixes must be applied to multiple places
- Obscures what's common versus what's environment-specific

**Decision**: Reject. Shared code with clear variation points is preferable to duplication.

## Decision 2: Implement Service-Mode Variables

Rather than a global `deployment_type` flag, we use service-specific mode variables that describe behavior, not environment:

```yaml
# Current pattern (environment-centric)
aws_deployment: "{{ deployment_type | trim | lower == 'aws' }}"

# New pattern (behavior-centric)
postgres_mode: cnpg          # Options: cnpg, external
object_storage_mode: minio   # Options: minio, s3, external
redis_mode: enterprise       # Options: enterprise, standalone, external
```

Benefits:
- Each dimension can vary independently
- Names describe behavior, not environment
- On-premise deployments that use external services work correctly
- AWS deployments that use some Scout-managed services work correctly

This pattern applies throughout the Ansible codebase:

1. **Roles route internally based on mode** rather than relying on playbook-level conditionals
2. **Clear interface variables for external services** define what's required when `mode: external`
3. **Computed uniform variables** allow downstream consumers to use `db_host` or `s3_endpoint` without knowing the source
4. **Templates stay clean** by moving conditional logic to defaults

### Alternatives Considered for Decision 2

#### Alternative 2a: Global Deployment Flag

**Description**: Continue using `aws_deployment` boolean to switch all behaviors.

**Advantages**:
- Simple to understand
- Single configuration point

**Disadvantages**:
- Binary flag doesn't capture multi-dimensional variation
- "AWS deployment" conflates many independent choices (database, storage, ingress)
- Can't express hybrid configurations (external database but Scout-managed storage)
- On-prem site with external PostgreSQL has no good option

**Decision**: Reject. Service-mode variables provide finer-grained control.

#### Alternative 2b: Boolean Feature Flags Per Service

**Description**: Use boolean flags like `use_external_postgres: true`, `use_external_redis: true`.

**Advantages**:
- More granular than a single deployment flag
- Boolean logic is simple

**Disadvantages**:
- Boolean explosion as options multiply (external vs managed is just one axis)
- Doesn't capture *how* something is deployed (CNPG vs standalone PostgreSQL)
- Still environment-centric thinking ("external" implies "not ours")
- Multiple booleans can conflict or create invalid combinations

**Decision**: Reject. Mode variables with explicit options are clearer and more extensible.

#### Alternative 2c: Environment-Specific Inventory Files

**Description**: Maintain separate inventory files per environment with all variables pre-set.

**Advantages**:
- Each environment fully specified in one place
- No conditional logic needed

**Disadvantages**:
- Massive duplication of common configuration
- Drift between environments as changes aren't propagated
- Unclear which variables are environment-specific versus universal
- Doesn't help roles be self-contained

**Decision**: Reject. Shared defaults with targeted overrides is more maintainable.

## Consequences

### From Decision 1 (Layered Model)

**Positive**:
1. **Clear mental model**: Layers help reason about what varies where and why
2. **Easier onboarding**: New platforms require implementing Layers 0-1; Layer 2 mostly works
3. **Team alignment**: Different teams or tools can own different layers
4. **Documentation clarity**: Can document each layer's responsibilities separately

**Negative**:
1. **Layer boundaries are fuzzy**: Platform services (Layer 1) can be "deployed by Scout" or "provided externally"—this ambiguity is inherent to the problem, not a flaw in the model
2. **Requires shared understanding**: Team must agree on the model for it to be useful
3. **Not a technical enforcement**: The layers are conceptual; nothing prevents mixing concerns

### From Decision 2 (Service-Mode Variables)

**Positive**:
1. **Independent variation**: Each service dimension can be configured independently
2. **Self-documenting**: Mode variables and interface variables make requirements explicit
3. **Reduced coupling**: Application layer doesn't need to know infrastructure details
4. **Hybrid support**: Mixed configurations (some external, some managed) work naturally

**Negative**:
1. **More variables**: Service-mode pattern introduces more configuration points than a single flag
2. **Migration effort**: Existing Ansible code must be refactored to use new patterns
3. **Learning curve**: Contributors must understand the mode pattern

## Implementation Guidance

### Applying the Layered Model

When adding a new component, determine its layer by asking:

| Question | If Yes → Layer |
|----------|----------------|
| Is this about the Kubernetes cluster itself, storage, or networking? | Layer 0: Infrastructure |
| Could a customer reasonably provide this externally? (databases, object storage, auth) | Layer 1: Platform Services |
| Is this core Scout functionality that users interact with? | Layer 2: Applications |

Layer 1 components should support both "deployed by Scout" and "provided externally" modes. Layer 2 components should aim for consistency across environments, with variation limited to feature flags and site-specific configuration.

### Service Mode Reference

#### PostgreSQL

| Mode | Description | Scout Deploys | Required Interface Variables |
|------|-------------|---------------|------------------------------|
| `cnpg` | CloudNativePG operator | Yes | None (uses inventory secrets) |
| `external` | External PostgreSQL (RDS, etc.) | No | `external_postgres_host`, `external_postgres_port`, `external_postgres_admin_user`, `external_postgres_admin_password` |

#### Object Storage

| Mode | Description | Scout Deploys | Required Interface Variables |
|------|-------------|---------------|------------------------------|
| `minio` | MinIO via operator | Yes | None (uses inventory secrets) |
| `s3` | AWS S3 | No | `s3_bucket_*` variables, IRSA role ARNs |
| `external` | Other S3-compatible | No | `external_s3_endpoint`, credentials |

#### Redis

| Mode | Description | Scout Deploys | Required Interface Variables |
|------|-------------|---------------|------------------------------|
| `enterprise` | Redis Enterprise operator | Yes | None |
| `standalone` | Single Redis instance | Yes | `redis_master_password` |
| `external` | External Redis/ElastiCache | No | `external_redis_host`, `external_redis_port`, `external_redis_password` |

### Pattern: Roles Route Internally

Each role validates its mode and routes to appropriate task files:

```yaml
# roles/minio/tasks/main.yaml
- name: Validate object_storage_mode
  ansible.builtin.assert:
    that:
      - object_storage_mode in ['minio', 's3', 'external']
    fail_msg: "object_storage_mode must be 'minio', 's3', or 'external'"

- name: Deploy MinIO
  ansible.builtin.include_tasks: deploy.yaml
  when: object_storage_mode == 'minio'

- name: Configure S3 access
  ansible.builtin.include_tasks: configure_s3.yaml
  when: object_storage_mode == 's3'
```

Playbooks become simpler—always include the role; let it decide what to do.

### Pattern: Computed Uniform Variables

Downstream roles should not need to know the source of a service:

```yaml
# scout_common/defaults/main.yaml
db_host: >-
  {{ external_postgres_host
     if postgres_mode == 'external'
     else (postgres_cluster_name ~ '-rw.' ~ postgres_cluster_namespace) }}

s3_endpoint: >-
  {{ ''
     if object_storage_mode == 's3'
     else ('http://minio.' ~ minio_tenant_namespace) }}
```

Consumers use `db_host`, `s3_endpoint`, etc. without knowing the implementation.

### Pattern: Clean Templates

Move conditional logic from templates to defaults:

```yaml
# Avoid (in template)
env:
  - name: S3_PATH_STYLE_ACCESS
    value: "{{ 'true' if not aws_deployment else 'false' }}"

# Prefer (in defaults)
s3_path_style_access: "{{ object_storage_mode == 'minio' }}"

# Template becomes simple
env:
  - name: S3_PATH_STYLE_ACCESS
    value: "{{ s3_path_style_access }}"
```

## Migration Path

This is an evolutionary change, not a breaking change. The patterns can be adopted incrementally:

| Step | Decision | Description |
|------|----------|-------------|
| 1 | D1 | Document layer ownership; align team on the model |
| 2 | D2 | Introduce mode variables that derive defaults from existing `aws_deployment` flag |
| 3 | D2 | Update roles one at a time to use internal mode routing |
| 4 | D2 | Simplify playbooks as roles become self-contained |
| 5 | D2 | Deprecate `aws_deployment` once all roles use mode variables |

## Summary

This ADR addresses deployment portability through two complementary decisions:

**Decision 1 (Layered Model)** provides the *mental model* — a way to reason about what should vary between environments (Layers 0-1) versus what should remain consistent (Layer 2). This helps teams make intentional choices about where variation belongs.

**Decision 2 (Service-Mode Variables)** provides the *implementation pattern* — a way to express that variation in Ansible code through behavior-centric mode variables rather than environment-centric flags. This enables independent configuration of each service dimension and supports hybrid deployments.

Together, these decisions enable Scout to be deployed across diverse environments (on-prem, AWS, existing clusters) without replacing our orchestration tooling or fragmenting the codebase.
