# ADR 0002: K3s Air-Gapped Deployment Strategy

**Date:** 2025-10-27  
**Status:** Accepted  
**Decision Owner**: TAG Team

## Context

Scout needs to support deployment in air-gapped environments where production k3s cluster nodes cannot access the internet. However, a staging server with internet access and Harbor registry is available, and production nodes can reach the staging server.

Key requirements:
- Install and upgrade k3s on nodes without internet access
- Leverage existing Harbor infrastructure on staging server
- Maintain backward compatibility with existing online deployments
- Minimize operational complexity and manual intervention

## Decision

We will implement air-gapped k3s installation using the following approach:

### 1. Harbor Pull-Through Proxy Cache for Container Images

**Decision:** Use Harbor's pull-through proxy cache feature for all container images (k3s system images and Scout application images).

**Rationale:**
- Harbor is already part of Scout's architecture (deployed via `playbooks/main.yaml`)
- Pull-through cache eliminates manual image staging and version tracking
- Staging server has internet access and can fetch images on-demand
- Production nodes pull through Harbor without needing direct internet access
- Single solution works for both k3s and Scout images
- Automatic caching reduces operational overhead

**Alternatives Considered:**
- Pre-staging images in Harbor: Requires manual download, extraction, and push for each version
- Airgap image tarballs on each node: Requires distributing large files to every node
- **Rejected because:** Pull-through cache is simpler and leverages existing infrastructure

### 2. Pre-Stage SELinux RPMs Using Kubernetes Job

**Decision:** Use a Kubernetes Job on the staging cluster to download `k3s-selinux` and its dependencies (including `container-selinux`) using yum with the Rancher k3s repository, then extract RPMs for distribution to air-gapped nodes.

**Rationale:**
- Air-gapped nodes cannot reach internet repositories
- `k3s-selinux` is hosted in Rancher's custom repository (rpm.rancher.io), not standard repos
- Using yum for download ensures automatic dependency resolution (no manual version tracking)
- Staging cluster is guaranteed available and online in air-gapped deployments
- Matches k3s install script's official repository configuration exactly

**Implementation Pattern:**
```yaml
Job with two containers:
  - initContainer (downloader): Configures Rancher repo, runs dnf download, exits
  - mainContainer (sleeper): Keeps pod running for file extraction
  - Shared emptyDir volume: Files accessible to both containers
```

**Process Flow:**
1. Ansible creates Kubernetes Job on staging cluster
2. Init container downloads RPMs to emptyDir volume and exits
3. Main container (sleep infinity) keeps pod running
4. Ansible extracts RPMs using `kubectl cp` from running pod
5. Ansible deletes Job (pod auto-cleaned)

**Alternatives Considered:**
- Manual RPM URL downloads: Requires tracking versions and URLs, prone to 404 errors
- hostPath volume: Requires node affinity and manual cleanup
- **Rejected because:** Init container pattern is simpler and more reliable

**Dependency Handling:**
- yum automatically resolves all dependencies during download
- No manual dependency tracking needed
- All packages guaranteed compatible (resolved by yum from same repository)

### 3. Temporary Artifact Staging (No Caching)

**Decision:** Download k3s artifacts (binary, install script, SELinux RPMs) to a temporary directory on the Ansible control node during playbook execution. Do not cache artifacts between runs.

**Rationale:**
- Simplifies artifact management - no persistent cache to maintain
- Ansible's `tempfile` module handles cleanup automatically
- Each playbook run gets fresh artifacts matching the specified version
- No risk of stale cached artifacts causing issues
- Reduces disk space requirements on control node

**Alternatives Considered:**
- Cache artifacts in role directory: Adds complexity for version management and cleanup
- **Rejected because:** Temporary staging is simpler and eliminates cache staleness issues

### 4. No Automatic Rollback

**Decision:** Do not implement automatic rollback capabilities for failed upgrades.

**Rationale:**
- Rollback adds significant complexity (backup management, version tracking, state recovery)
- Upgrades can be tested in staging environment before production
- Manual recovery options are sufficient:
  - Re-run playbook with previous version
  - Restore from system snapshots if available
  - Reinstall from scratch using k3s uninstall script
- Automatic rollback provides limited benefit given testing requirements

**Future Consideration:**
- If automated rollback becomes a frequent operational need, it can be added in a future iteration

### 5. Backward Compatibility (Feature Flag)

**Decision:** Use `air_gapped` inventory variable as a feature flag to enable air-gapped mode. When `air_gapped=false` (default), use existing online installation behavior.

**Rationale:**
- Maintains compatibility with existing Scout deployments
- Clear opt-in mechanism for air-gapped installations
- Allows gradual migration and testing
- CI continues to test online mode by default

## Consequences

### Positive

- **Simplified operations:** Harbor automatically caches images; no manual image staging
- **Reduced complexity:** Temporary artifacts eliminate cache management
- **Automatic dependency resolution:** yum handles all SELinux package dependencies automatically
- **No version maintenance:** No hardcoded RPM versions or URLs to update
- **Reliable SELinux handling:** Packages guaranteed compatible via yum resolution
- **Backward compatible:** Existing deployments unaffected
- **Leverages existing infrastructure:** Uses staging cluster already required for Harbor

### Negative

- **No automatic rollback:** Failed upgrades require manual recovery
- **Harbor dependency:** Harbor must be deployed before k3s (already true for Scout in air-gapped mode)
- **Staging cluster dependency:** Staging k3s cluster must be running for SELinux package downloads
- **Kubernetes client requirement:** Ansible control node needs kubectl and kubernetes.core collection

### Risks and Mitigations

| Risk | Mitigation |
|------|-----------|
| Harbor unavailable during installation | Harbor deployed first via main playbook; clear dependencies |
| Staging cluster unavailable | Ensure staging k3s cluster is running before k3s_airgap role executes |
| Kubernetes Job fails to download packages | Job logs available via kubectl; automatic retry via Job backoffLimit |
| Failed upgrades difficult to recover | Always test in staging first; document manual recovery |

## Out of Scope

The following are explicitly out of scope for this implementation:

- **Network policies and firewall rules:** Handled externally; not part of Ansible automation
- **Version compatibility checking:** Manual verification when updating versions
- **Automatic artifact version discovery:** Versions specified explicitly in role defaults

## Implementation Notes

- Target k3s version: v1.33.5+k3s1 (current stable, auto-detected if not specified)
- SELinux packages resolved automatically from Rancher k3s repository (stable channel)
- Kubernetes Job runs on staging cluster in `default` namespace
- Job uses Rocky Linux 9 container image for package downloads
- All artifacts downloaded to Ansible control node temporary directory
- Temporary directory automatically created/cleaned by Ansible
- SELinux package installation conditional on `ansible_selinux.status == 'enabled'`
- Requires `kubernetes.core` Ansible collection and kubectl on staging node

## References

- [K3s Air-Gapped Installation Docs](https://docs.k3s.io/installation/airgap)
- [K3s Upgrades Documentation](https://docs.k3s.io/upgrades)
- [Harbor Documentation](https://goharbor.io/docs/latest/)
