# k3s_airgap

Air-gapped k3s preparation role for Scout deployment.

## Overview

This role handles **preparation** for k3s installation in air-gapped environments by:
1. Downloading k3s artifacts to the Ansible control node (one-time)
2. Distributing artifacts (binary and install script) to target nodes
3. Installing SELinux packages when required (auto-detected)

**Note:** The actual k3s installation (server and agent) is handled by the `k3s.yaml` playbook, not this role. This role only prepares the environment.

## Requirements

- Ansible control node must have internet access
- Target nodes must be Rocky Linux 9 / RHEL 9
- Python 3 installed on target nodes
- `air_gapped` variable must be set to `true` in inventory

## Role Variables

See `defaults/main.yaml` for all available variables.

### Version Configuration

- `k3s_version`: k3s version to install (default: v1.33.5+k3s1)
- `k3s_selinux_rpm_version`: k3s-selinux RPM version (default: "1.6-1")
- `container_selinux_rpm_version`: container-selinux RPM version (default: "2.229.0-1.el9")

### Installation Configuration

- `k3s_selinux_enabled`: Auto-detected based on target node SELinux status (can override in inventory)
- `k3s_artifact_download_timeout`: Download timeout in seconds (default: 300)
- `k3s_binary_path`: Where to install k3s binary (default: /usr/local/bin/k3s)
- `k3s_install_script_path`: Where to install k3s installation script (default: /root/bin/get.k3s.io.sh - same as online mode)

## Task Files

The role is organized into three task files:

### main.yaml
Entry point that orchestrates the preparation:
- Downloads artifacts to control node (run_once)
- Copies k3s binary to target nodes
- Copies install script to target nodes
- Includes SELinux package installation (if enabled)

### download_artifacts.yaml
Downloads k3s artifacts to Ansible control node:
- Creates temporary directory for staging
- Downloads k3s binary from GitHub releases
- Downloads k3s install script from get.k3s.io
- Downloads SELinux RPMs (if SELinux enabled)
- Sets fact with temp directory path for distribution

### selinux.yaml
Installs SELinux packages when enabled:
- Copies RPMs to target nodes
- Installs container-selinux and k3s-selinux using yum
- Verifies SELinux policy module loaded
- Cleans up RPM files after installation

## Dependencies

None. This role is self-contained.

## Example Playbook

### Basic Usage (Preparation Only)

```yaml
- hosts: k3s_cluster
  roles:
    - k3s_airgap
  vars:
    air_gapped: true
    k3s_version: v1.33.5+k3s1
```

### Integration with k3s.yaml Playbook

The role is designed to be used as part of the Scout k3s.yaml playbook:

```yaml
# Preparation phase (this role)
- name: Prepare for air-gapped installation
  hosts: k3s_cluster
  gather_facts: true
  tasks:
    - name: Include k3s_airgap role for air-gapped installation
      ansible.builtin.include_role:
        name: k3s_airgap
      when: air_gapped | default(false) | bool

# Installation phase (handled by k3s.yaml playbook)
- hosts: server
  name: Install k3s on server node
  tasks:
    - name: Install k3s server
      ansible.builtin.command:
        cmd: /root/bin/get.k3s.io.sh
        creates: '/usr/local/bin/k3s-uninstall.sh'
      environment:
        INSTALL_K3S_SKIP_DOWNLOAD: "{{ 'true' if air_gapped | default(false) | bool else omit }}"
        INSTALL_K3S_VERSION: '{{ k3s_version | default("") }}'
        K3S_TOKEN: '{{ k3s_token }}'
```

## Inventory Example

```yaml
all:
  children:
    k3s_cluster:
      vars:
        air_gapped: true
        k3s_version: v1.33.5+k3s1
        k3s_token: my-secret-token
        base_dir: /var/lib/rancher/k3s
        use_staging_node: true  # If using Harbor registry
      children:
        server:
          hosts:
            k3s-server-01:
        agents:
          hosts:
            k3s-agent-01:
            k3s-agent-02:
```

## Testing

### Molecule Tests

Run Molecule tests to validate the role logic:

```bash
cd roles/k3s_airgap
uvx --with kubernetes molecule test
```

Tests validate:
- Artifact download from internet
- Temporary directory creation and fact sharing
- SELinux conditional logic
- Role variable definitions

## How It Works

### Preparation Flow

1. **Artifact Download (control node, run_once)**
   - Role downloads k3s binary, install script, and SELinux RPMs to temp directory
   - Temp directory path is shared with all hosts via fact

2. **Binary Distribution (all nodes)**
   - k3s binary copied to `/usr/local/bin/k3s`
   - Install script copied to `/root/bin/get.k3s.io.sh` (same path as online mode)
   - Both files set to executable (mode 0755)

3. **SELinux Installation (conditional, all nodes)**
   - If SELinux is enabled on target nodes:
     - Copy SELinux RPMs to nodes
     - Install using `yum` module (handles dependencies)
     - Verify k3s SELinux module loaded
     - Clean up RPM files

### Installation Flow (handled by k3s.yaml playbook)

4. **Server Installation (server nodes)**
   - k3s.yaml playbook runs `/root/bin/get.k3s.io.sh` (same script path for both online and air-gapped modes)
   - In air-gapped mode: Environment variable `INSTALL_K3S_SKIP_DOWNLOAD=true` prevents download
   - k3s binary already in place from step 2

5. **Agent Installation (agent nodes)**
   - k3s.yaml playbook runs `/root/bin/get.k3s.io.sh` on agents
   - In air-gapped mode: Same skip-download approach as server

## Integration with Scout

This role is part of Scout's k3s deployment infrastructure. It's automatically included in the `playbooks/k3s.yaml` playbook when `air_gapped=true` is set in inventory.

The k3s.yaml playbook handles:
- Registry mirror configuration (for Harbor)
- k3s server installation
- k3s agent installation
- Service management
- Kubeconfig setup

This role only handles **preparation** (artifacts and SELinux).

## License

See Scout project license.

## Author

Scout Development Team
