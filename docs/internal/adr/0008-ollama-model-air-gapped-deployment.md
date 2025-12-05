# ADR 0008: Ollama Model Distribution in Air-Gapped Environments

**Date**: 2025-12-04  
**Status**: Proposed  
**Decision Owner**: TAG Team

## Context

Scout's optional Chat feature (Open WebUI + Ollama) requires large language models to be downloaded from Ollama's registry (`registry.ollama.ai`). In air-gapped deployments where production nodes cannot access the internet, this presents a challenge similar to container image distribution—but with key differences.

### Current Architecture

In air-gapped Scout deployments:
- **Container images**: Proxied through Harbor on the staging node (pull-through cache)
- **Helm charts**: Deployed remotely from staging node via kubeconfig (ADR 0001)
- **SELinux RPMs**: Downloaded on staging via Kubernetes Job, transferred to production (ADR 0002)
- **Ollama models**: Currently pulled directly from `registry.ollama.ai` via Kubernetes Job

```
Current Model Pull (Online):

Production Ollama Pod → registry.ollama.ai → Model Downloaded
       ↓
    Stored in PVC (/root/.ollama/models)
```

### Problem

The current `pull_models.yaml` task creates a Kubernetes Job that runs `ollama pull` commands:

```yaml
- name: Create model pull Job
  kubernetes.core.k8s:
    definition:
      spec:
        containers:
          - command:
              - /bin/sh
              - -c
              - |
                ollama pull {{ model }}
```

This requires the production cluster to reach `registry.ollama.ai`, which violates air-gap requirements.

### Why Harbor Cannot Help

Harbor's pull-through proxy only supports OCI-compatible container image registries. Ollama's registry protocol resembles but is not fully compatible with the Docker/OCI registry API, and Ollama does not currently support custom registries or mirrors ([GitHub Issue #2388](https://github.com/ollama/ollama/issues/2388)). Standard Docker registry proxies do not work with Ollama models.

### Model Storage Structure

Ollama models are stored in `~/.ollama/models/` with two subdirectories:
- **`blobs/`**: Content-addressable model weights (large files, shared across models)
- **`manifests/`**: Registry metadata organized by model name (small files)

This structure is [officially supported for manual transfer](https://github.com/ollama/ollama/issues/3441) between machines. The blobs are content-addressable, so copying is safe and idempotent.

## Decision

**Pre-stage Ollama models to shared NFS storage from the staging cluster, then mount on production.**

This leverages existing shared NFS storage accessible from both staging and production nodes, avoiding large file transfers through the jump node.

### Architecture

```
                         Internet
                            │
                            │
                ┌───────────▼────────────┐
                │  Staging Node          │
                │  ┌──────────────────┐  │
                │  │ K3s (standalone) │  │
                │  │  ┌────────────┐  │  │
                │  │  │  Ollama    │  │  │  ← Pull models to NFS
                │  │  │  Job       │  │  │
                │  │  └────────────┘  │  │
                │  └────────┬─────────┘  │
                └───────────┼────────────┘
                            │
                    ┌───────▼───────┐
                    │  Shared NFS   │  ← Models stored here
                    │  /nfs/ollama  │
                    └───────┬───────┘
                            │
                ┌───────────▼────────────┐
                │  Air-Gapped Cluster    │
                │                        │
                │  Production Ollama     │  ← Reads models from NFS
                │  (mounts NFS)          │
                └────────────────────────┘
```

### Why NFS Works for Ollama Models

The concern about network filesystems in ADR 0006 (Jupyter) was specifically about **SQLite file locking**, which fails on NFS. Ollama models are different:

- **Write once**: Models downloaded once during staging
- **Read many**: Production only reads model blobs during inference
- **No locking**: Content-addressable blobs don't require file locks
- **Large sequential reads**: NFS handles large file reads well

### Implementation

#### Step 1: Pull Models to NFS on Staging

Create a Kubernetes Job on the staging cluster that pulls models directly to the shared NFS path:

```yaml
# Staging cluster - model download Job
apiVersion: batch/v1
kind: Job
metadata:
  name: ollama-download-models
  namespace: ollama-staging
spec:
  ttlSecondsAfterFinished: 3600  # Clean up 1 hour after completion
  backoffLimit: 2
  template:
    spec:
      restartPolicy: OnFailure
      containers:
        - name: ollama
          image: ollama/ollama:latest
          env:
            - name: OLLAMA_MODELS
              value: /nfs/ollama/models
          command:
            - /bin/sh
            - -c
            - |
              ollama serve &
              sleep 5
              {% for model in ollama_models %}
              echo "Pulling {{ model }}..."
              ollama pull {{ model }}
              {% endfor %}
              echo "All models pulled successfully"
          volumeMounts:
            - name: nfs-models
              mountPath: /nfs/ollama
      volumes:
        - name: nfs-models
          hostPath:
            path: {{ ollama_nfs_path }}  # e.g., /mnt/nfs/ollama
            type: DirectoryOrCreate
```

#### Step 2: Configure Production Ollama to Use NFS

Production Ollama mounts the same NFS path and uses `OLLAMA_MODELS` to point to it:

```yaml
# Production Ollama Helm values
ollama:
  extraEnv:
    - name: OLLAMA_MODELS
      value: /nfs/ollama/models
  volumes:
    - name: nfs-models
      hostPath:
        path: {{ ollama_nfs_path }}
        type: Directory
  volumeMounts:
    - name: nfs-models
      mountPath: /nfs/ollama
      readOnly: true  # Production only reads
```

### Workflow

```
1. Ansible starts Job on staging cluster
   └── Job pulls models from registry.ollama.ai to NFS

2. Ansible waits for Job completion
   └── Models now available on shared NFS

3. Ansible deploys production Ollama
   └── Configured to read models from NFS path

4. Production Ollama starts with models available
   └── No file transfer required, no internet access needed
```

### Configuration Variables

```yaml
# inventory.yaml
air_gapped: true
enable_chat: true

# Shared NFS path (required for air-gapped, optional otherwise)
ollama_nfs_path: /mnt/nfs/ollama

# Models to pull/pre-stage
ollama_models:
  - gpt-oss:120b
  - llama3:8b
```

### Deployment Mode Selection

| `air_gapped` | `ollama_nfs_path` set | Behavior |
|--------------|----------------------|----------|
| `false` | (ignored) | **Online**: Production Job pulls directly from `registry.ollama.ai` (current behavior) |
| `true` | Yes | **NFS**: Staging Job pulls to NFS, production mounts read-only |
| `true` | No | **Error**: Deployment fails with message to configure `ollama_nfs_path` |

This logic ensures:
- Non-air-gapped deployments work unchanged
- Air-gapped deployments require shared NFS storage

## Alternatives Considered

### Summary

| Alternative | Verdict |
|-------------|---------|
| **1. Shared NFS Storage** | **Selected for air-gapped** - Simple, leverages existing infra |
| 2. Pre-stage + kubectl cp Transfer | Rejected - Large transfers through jump node, complexity |
| 3. Ollama Registry Pull-Through Proxy | Rejected - Adds third-party dependency |
| 4. HTTPS_PROXY to Staging | Rejected - Creates persistent network path |
| 5. Manual Model Transfer | Rejected - Operator burden |

**Note**: Non-air-gapped deployments use existing behavior (production Job pulls directly from `registry.ollama.ai`).

### Alternative 1: Shared NFS Storage (Selected)

Pull models to shared NFS from staging, mount read-only on production.

**Pros:**
- No file transfers through jump node
- Leverages existing NFS infrastructure
- Models pulled once, immediately available to production
- No new dependencies or services
- Production mounts read-only (secure)

**Cons:**
- Requires shared NFS accessible from both staging and production
- NFS performance for initial model load (mitigated by caching)

**Verdict:** Selected - simplest approach given existing NFS infrastructure.

### Alternative 2: Pre-stage + kubectl cp Transfer (Rejected)

Download models on staging, transfer to production via kubectl cp through jump node.

**Pros:**
- Works without shared storage
- No persistent network path from production to internet
- No NFS infrastructure required

**Cons:**
- Large file transfers (models are 4-240GB) through jump node
- Temporary storage needed on Ansible control node
- Slower than shared storage approach
- Added complexity for minimal benefit given NFS availability

**Verdict:** Rejected - complexity and large file transfers not justified when NFS is available.

### Alternative 3: Ollama Registry Pull-Through Proxy

Deploy [ollama-registry-pull-through-proxy](https://github.com/simonfrey/ollama-registry-pull-through-proxy) on staging to cache models.

**Pros:**
- Matches Harbor pattern (pull-through cache)
- Automatic caching on first pull

**Cons:**
- Third-party dependency (less mature project)
- Requires HTTP (insecure) or custom TLS setup
- Additional service to maintain
- Creates persistent network path from production to staging
- Production can still "pull" models at runtime (less controlled)

**Verdict:** Rejected - adds unnecessary dependency when simpler options exist.

### Alternative 4: HTTPS_PROXY to Staging Squid

Configure production Ollama with `HTTPS_PROXY` pointing to a Squid proxy on staging.

**Pros:**
- Uses standard proxy mechanism
- No custom Ollama configuration needed

**Cons:**
- Creates persistent network path from production through staging to internet
- Staging becomes a proxy for arbitrary HTTPS traffic
- Less secure than one-time pull
- Requires maintaining Squid proxy

**Verdict:** Rejected - creates persistent network path, less secure.

### Alternative 5: Manual Model Transfer

Document manual process for operators to download and transfer models.

**Pros:**
- No automation required
- Full operator control

**Cons:**
- Error-prone and time-consuming
- Knowledge required for model file locations
- Not repeatable or auditable

**Verdict:** Rejected - operator burden too high.

## Consequences

### Positive

1. **No new dependencies**: Uses existing NFS infrastructure and Ansible tooling
2. **No large transfers**: Models written to NFS once, no copying through jump node
3. **More secure**: No persistent network path from production to internet
4. **Controlled**: Models pulled at deploy time, production mounts read-only
5. **Simple**: Straightforward NFS mount, no custom protocols
6. **Efficient**: Models immediately available after staging pull completes

### Negative

1. **NFS dependency**: Requires shared NFS accessible from both clusters
2. **Re-deployment for new models**: Adding models requires re-running staging job
3. **NFS performance**: Initial model load reads from network (mitigated by OS caching)

### Risks and Mitigations

| Risk | Mitigation |
|------|-----------|
| NFS unavailable | Production Ollama fails to start; monitor NFS health |
| NFS performance issues | Large sequential reads perform well; OS caches frequently-used data |
| Model corruption | Ollama blobs are content-addressable; re-pull if checksum fails |
| Staging/prod path mismatch | Use consistent `ollama_nfs_path` variable across both |

## Implementation Notes

### Deployment Order

1. Deploy staging k3s (existing)
2. Deploy Harbor (existing)
3. **Pull Ollama models to NFS from staging** (new)
4. Deploy production k3s with registry mirrors (existing)
5. Deploy Scout services including Ollama with NFS mount (modified)

### Storage Requirements

**Shared NFS:**
- Size: ~500GB for large deployments (gpt-oss:120b + additional models)
- Must be mounted on both staging and production nodes at same path
- Staging needs write access, production needs read access

**Production cluster:**
- No local Ollama PVC needed for models (reads from NFS)
- May still need small PVC for Ollama runtime state (if any)

### Model Size Estimates

| Model | Size |
|-------|------|
| llama3:8b | ~4.7GB |
| llama3:70b | ~40GB |
| gpt-oss:120b (default Scout model) | ~60-240GB |

### Ansible Role Changes

**Modify open-webui role** (`ansible/roles/open-webui/tasks/pull_models.yaml`):

```yaml
# Online: pull directly from registry (current behavior)
- name: Pull models via Job
  ansible.builtin.include_tasks: pull_models_online.yaml
  when: not (air_gapped | default(false) | bool)

# Air-gapped with NFS: pull to NFS on staging
- name: Pull models to NFS from staging
  ansible.builtin.include_tasks: pull_models_nfs.yaml
  when:
    - air_gapped | default(false) | bool
    - ollama_nfs_path is defined and ollama_nfs_path | length > 0

# Air-gapped without NFS: not supported
- name: Warn if air-gapped without NFS
  ansible.builtin.fail:
    msg: >-
      Air-gapped deployment requires ollama_nfs_path to be set.
      Configure shared NFS storage for Ollama models between staging and cluster.
  when:
    - air_gapped | default(false) | bool
    - ollama_nfs_path is not defined or ollama_nfs_path | length == 0
```

**Modify open-webui role** (`ansible/roles/open-webui/templates/values.yaml.j2`):

```yaml
ollama:
{% if air_gapped | default(false) and ollama_nfs_path is defined and ollama_nfs_path | length > 0 %}
  # Air-gapped with NFS: mount models read-only
  extraEnv:
    - name: OLLAMA_MODELS
      value: /nfs/ollama/models
  volumes:
    - name: nfs-models
      hostPath:
        path: {{ ollama_nfs_path }}
        type: Directory
  volumeMounts:
    - name: nfs-models
      mountPath: /nfs/ollama
      readOnly: true
{% endif %}
  # Online: models pulled to default PVC location
```

### NFS Directory Structure

```
{{ ollama_nfs_path }}/
└── models/
    ├── blobs/
    │   ├── sha256-abc123...  # Model weight files
    │   └── sha256-def456...
    └── manifests/
        └── registry.ollama.ai/
            └── library/
                ├── gpt-oss/
                │   └── 120b
                └── llama3/
                    └── 8b
```

### Future Enhancements

- **Model verification**: Checksum validation after pull
- **Cleanup automation**: Remove unused model blobs
- **Multiple model versions**: Support for staging different versions

## Out of Scope

- **Model scanning/validation**: Trust upstream Ollama registry
- **Custom model hosting**: Only supports public Ollama registry models
- **Automatic model updates**: Manual re-deployment required for updates

## References

- [Ollama Model Transfer (GitHub Issue #3441)](https://github.com/ollama/ollama/issues/3441)
- [Ollama OLLAMA_MODELS Environment Variable](https://github.com/ollama/ollama/blob/main/docs/faq.md#how-do-i-configure-ollama-server)
- [Transferring Ollama Model Files](https://ai.gopubby.com/transferring-and-managing-ollama-model-files-880ac6c34235)
- [Air-Gapped AI Setup Guide](https://markaicode.com/air-gapped-ai-setup-ollama-secure-environments/)
- ADR 0001: Helm Deployment for Air-Gapped Environments
- ADR 0002: K3s Air-Gapped Deployment Strategy
- ADR 0006: Jupyter Node Pinning (explains NFS/SQLite incompatibility - not applicable to Ollama)
- ADR 0007: Separation of Jump Node and Staging Node
