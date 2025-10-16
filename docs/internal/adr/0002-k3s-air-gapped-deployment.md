# ADR 0002: K3s Air-Gapped Deployment Strategy

**Date:** 2025-10-15
**Status:** Proposed
**Deciders:** TAG Team

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

### 2. Pre-Stage SELinux RPMs (Download and Install Locally)

**Decision:** Download both `k3s-selinux` and `container-selinux` RPM files locally and install them using the `yum` command with local package file paths.

**Rationale:**
- Air-gapped nodes cannot reach internet repositories
- `k3s-selinux` is not in standard Rocky/RHEL repositories (only GitHub releases)
- `container-selinux` is in AppStream but inaccessible without local repo mirrors
- Pre-staging both RPMs ensures consistent, predictable installation
- Works regardless of node repository configuration

**Alternatives Considered:**
- Host a yum repository on the staging node: Adds significant complexity for setup and maintenance
- **Rejected because:** Hosting a repository is overly complex for this use case

**Dependency Handling:**
- If RPM installation fails due to missing dependencies (e.g., `selinux-policy` version mismatch), handle outside Ansible playbooks
- This is an accepted tradeoff to avoid complex dependency resolution in Ansible

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
- **Reliable SELinux handling:** Pre-staged RPMs work regardless of node configuration
- **Backward compatible:** Existing deployments unaffected
- **Leverages existing infrastructure:** Harbor already deployed for Scout

### Negative

- **No automatic rollback:** Failed upgrades require manual recovery
- **Harbor dependency:** Harbor must be deployed before k3s (already true for Scout in air-gapped mode)
- **Manual dependency resolution:** SELinux dependency failures require manual intervention
- **Version maintenance:** `container-selinux` version must be updated in role defaults

### Risks and Mitigations

| Risk | Mitigation |
|------|-----------|
| Harbor unavailable during installation | Harbor deployed first via main playbook; clear dependencies |
| SELinux dependency failures | Document recovery procedures; acceptable for rare edge cases |
| Failed upgrades difficult to recover | Always test in staging first; document manual recovery |

## Out of Scope

The following are explicitly out of scope for this implementation:

- **Network policies and firewall rules:** Handled externally; not part of Ansible automation
- **Version compatibility checking:** Manual verification when updating versions
- **Automatic artifact version discovery:** Versions specified explicitly in role defaults

## Implementation Notes

- Target k3s version: v1.33.5+k3s1 (current stable)
- Target k3s-selinux version: 1.6-1 (current stable)
- Target container-selinux version: 2.229.0-1.el9 (configurable in role defaults)
- All artifacts downloaded on Ansible control node (requires internet access)
- Temporary directory automatically created/cleaned by Ansible
- SELinux package installation conditional on `ansible_selinux.status == 'enabled'`

## References

- [K3s Air-Gapped Installation Docs](https://docs.k3s.io/installation/airgap)
- [K3s Upgrades Documentation](https://docs.k3s.io/upgrades)
- [Harbor Documentation](https://goharbor.io/docs/latest/)
