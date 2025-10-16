# k3s_airgap Role

Downloads k3s artifacts to Ansible control node for air-gapped installation.

## Requirements

- Ansible 2.9+
- Internet access on Ansible control node
- Python 3.6+ on remote hosts

## Role Variables

See `defaults/main.yaml` for all configurable variables.

Key variables:
- `k3s_version`: k3s version to install (default: v1.33.5+k3s1)
- `k3s_selinux_enabled`: Auto-detected or set explicitly

## Dependencies

None

## Example Playbook

```yaml
- hosts: k3s_cluster
  roles:
    - k3s_airgap
```

## Testing

```bash
cd roles/k3s_airgap
uvx --with kubernetes molecule test
```

## Phase 0 Validation

This role uses the pattern validated in Phase 0 spike research.
See: docs/internal/phase-0-spike-subagent.md
