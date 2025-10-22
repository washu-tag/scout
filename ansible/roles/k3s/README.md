# k3s Role

This role installs and configures [k3s](https://k3s.io/) - a lightweight Kubernetes distribution - on target nodes. It supports both online (internet-connected) and air-gapped (offline) deployment modes.

## Features

- **Online Installation**: Downloads k3s directly from the internet using the official install script
- **Air-Gapped Installation**: Downloads artifacts to Ansible control node and deploys without internet access
- **SELinux Support**: Automatically installs k3s-selinux and container-selinux packages when SELinux is enabled
- **Harbor Registry Mirrors**: Configures k3s to use Harbor pull-through proxy for air-gapped deployments
- **GPU Worker Support**: Configures NVIDIA container runtime for GPU-enabled nodes
- **Multiple Node Types**: Supports server (control plane), agent (worker), and GPU worker nodes

## Requirements

### Control Node Requirements

- Ansible 2.9+
- `kubernetes.core` collection (for k8s modules)
- Internet access (for online mode or to download air-gapped artifacts)

### Target Node Requirements

- Rocky Linux 9 (or compatible RHEL-based distribution)
- Python 3
- `python3-kubernetes` package (installed automatically by role)
- For air-gapped mode: Staging cluster with Harbor registry

### Inventory Groups

This role expects hosts to be organized into the following groups:

- `k3s_cluster`: All nodes (servers and agents)
- `server`: Control plane node(s) - typically a single server
- `agents`: Worker nodes
- `gpu_workers`: GPU-enabled worker nodes (optional)
- `staging`: Staging cluster for air-gapped artifact downloads (required for air-gapped mode)

## Role Variables

### Version Configuration

| Variable | Default | Description |
|----------|---------|-------------|
| `k3s_version` | `''` | k3s version to install. Empty string auto-detects latest stable |
| `k3s_install_script_url` | `https://get.k3s.io` | URL to k3s installation script |
| `k3s_binary_base_url` | `https://github.com/k3s-io/k3s/releases/download` | Base URL for k3s binary downloads |

### Deployment Mode

| Variable | Default | Description |
|----------|---------|-------------|
| `air_gapped` | `false` | Enable air-gapped installation mode |
| `k3s_token` | `''` | **Required** Cluster join token for all servers and agents |
| `base_dir` | `/var/lib/rancher/k3s` | k3s data directory |
| `kubeconfig_yaml` | `/etc/rancher/k3s/k3s.yaml` | Path to kubeconfig file |
| `kubeconfig_group` | `wheel` | Group ownership for kubeconfig |

### Air-Gapped Configuration

| Variable | Default | Description |
|----------|---------|-------------|
| `k3s_artifact_download_timeout` | `300` | Timeout for artifact downloads (seconds) |
| `k3s_binary_path` | `/usr/local/bin/k3s` | Where to install k3s binary |
| `k3s_install_script_path` | `/root/bin/get.k3s.io.sh` | Where to install k3s script |

### SELinux Configuration

| Variable | Default | Description |
|----------|---------|-------------|
| `k3s_selinux_enabled` | Auto-detect | Install SELinux packages if SELinux is enabled |
| `k3s_selinux_channel` | `stable` | Rancher repo channel (stable/testing/latest) |
| `k3s_selinux_rpm_site` | `rpm.rancher.io` | Rancher RPM repository site |
| `k3s_selinux_rpm_staging_dir` | `/tmp/k3s-selinux-rpms` | Temporary directory for RPM staging |

### Kubernetes Job Configuration

| Variable | Default | Description |
|----------|---------|-------------|
| `k3s_package_download_namespace` | `default` | Namespace for SELinux download Job on staging cluster |
| `k3s_package_download_timeout` | `300` | Timeout for Job completion (seconds) |

## Dependencies

- `scout_common` role (for Helm chart deployment patterns)
- `harbor` role variables (for air-gapped registry mirror configuration)

## Example Inventory

### Online Mode

```yaml
all:
  vars:
    k3s_token: my-secret-token-12345
    k3s_version: v1.30.0+k3s1  # Optional - leave empty for auto-detect
    air_gapped: false

  children:
    k3s_cluster:
      children:
        server:
          hosts:
            k3s-server.example.com:
        agents:
          hosts:
            k3s-agent-1.example.com:
            k3s-agent-2.example.com:
```

### Air-Gapped Mode

```yaml
all:
  vars:
    k3s_token: my-secret-token-12345
    k3s_version: v1.30.0+k3s1
    air_gapped: true

  children:
    staging:
      hosts:
        staging.example.com:
          kubeconfig_yaml: /path/to/staging/kubeconfig

    k3s_cluster:
      children:
        server:
          hosts:
            airgap-server.example.com:
        agents:
          hosts:
            airgap-agent-1.example.com:
        gpu_workers:
          hosts:
            airgap-gpu-1.example.com:
```

## Example Playbook

### Basic Usage

```yaml
---
- name: Install k3s cluster
  hosts: k3s_cluster
  roles:
    - k3s
```

### With Role in Server/Agent Plays

```yaml
---
- name: Install k3s on server
  hosts: server
  roles:
    - scout_common
    - k3s

- name: Install k3s on agents
  hosts: agents
  roles:
    - k3s

- name: Configure GPU workers
  hosts: gpu_workers
  roles:
    - k3s
```

## How It Works

### Online Mode (air_gapped=false)

1. **Prerequisites**: Creates directories, installs python3-kubernetes, downloads install script
2. **Server Installation**: Runs get.k3s.io script to install k3s server
3. **Agent Installation**: Runs get.k3s.io script to install k3s agent and join cluster
4. **GPU Configuration**: Installs NVIDIA container toolkit (if gpu_workers group)

### Air-Gapped Mode (air_gapped=true)

1. **Prerequisites**: Creates directories, installs python3-kubernetes
2. **Artifact Download** (on Ansible control node):
   - Auto-detects latest k3s version if not specified
   - Downloads k3s binary from GitHub
   - Downloads get.k3s.io install script
   - Downloads SELinux RPMs via Kubernetes Job on staging cluster (if SELinux enabled)
3. **Artifact Distribution**: Copies k3s binary and install script to all target nodes
4. **SELinux Installation**: Installs k3s-selinux and container-selinux packages (if enabled)
5. **Registry Configuration**: Configures Harbor pull-through proxy as container registry mirror
6. **Server Installation**: Runs install script with INSTALL_K3S_SKIP_DOWNLOAD=true
7. **Agent Installation**: Same as server but joins cluster via K3S_URL
8. **GPU Configuration**: Installs NVIDIA container toolkit (if gpu_workers group)

### SELinux Package Download (Air-Gapped)

The role uses a unique approach to download SELinux packages in air-gapped environments:

1. Creates a Kubernetes Job on the staging cluster
2. Job runs Rocky Linux 9 container with yum
3. Container downloads k3s-selinux and container-selinux with dependencies
4. Extracts RPMs from container using `kubectl cp`
5. Fetches RPMs from staging node to Ansible control node
6. Distributes RPMs to target nodes and installs via yum

This approach ensures correct SELinux package versions for Rocky Linux 9 without requiring yum repos on air-gapped nodes.

## Task Organization

The role is organized into focused task files:

- `main.yaml`: Orchestration - coordinates all installation steps
- `prerequisites.yaml`: Common setup for all nodes
- `artifacts.yaml`: Handles artifact download for air-gapped mode
- `download_selinux_rpms.yaml`: Kubernetes Job for SELinux package download
- `selinux.yaml`: SELinux package installation
- `registry.yaml`: Harbor registry mirror configuration
- `server.yaml`: k3s server (control plane) installation
- `agent.yaml`: k3s agent (worker) installation
- `gpu.yaml`: GPU worker configuration

## Testing

This role includes a comprehensive Molecule test suite:

```bash
cd ansible/roles/k3s
molecule test
```

The tests verify:
- Default variable values
- Online vs air-gapped mode conditionals
- SELinux auto-detection
- Registry mirror configuration logic
- Group membership detection (server/agents/gpu_workers)
- Installation path defaults
- Timeout configurations
- Version auto-detection logic

Tests use mocks to verify role logic without actually installing k3s.

## Troubleshooting

### k3s installation fails with permission denied

**Problem**: Install script cannot create files or directories

**Solution**: Ensure target user has sudo privileges or run playbook with `--become`

### SELinux package download Job fails

**Problem**: Kubernetes Job on staging cluster fails to download RPMs

**Solution**:
- Check staging cluster connectivity: `kubectl --kubeconfig=/path/to/staging/config get nodes`
- Check Job logs: `kubectl logs -n default <job-pod-name> -c downloader`
- Verify internet access from staging cluster
- Increase timeout: `k3s_package_download_timeout: 600`

### Registry mirror not working in air-gapped mode

**Problem**: Pods fail to pull images even with Harbor configured

**Solution**:
- Verify Harbor is running on staging node
- Check `/etc/rancher/k3s/registries.yaml` on target nodes
- Verify Harbor has pull-through projects configured
- Restart k3s: `systemctl restart k3s` (or `k3s-agent`)

### Agent nodes fail to join cluster

**Problem**: Agents cannot connect to server

**Solution**:
- Verify `k3s_token` matches on server and agents
- Check server hostname/IP is reachable from agents: `ping <server-hostname>`
- Verify port 6443 is open: `telnet <server-hostname> 6443`
- Check server is ready: `kubectl get nodes` on server

### GPU runtime not working

**Problem**: GPU pods fail to schedule or cannot access GPUs

**Solution**:
- Verify NVIDIA drivers are installed on GPU nodes
- Check NVIDIA container toolkit: `nvidia-ctk --version`
- Verify `/etc/rancher/k3s/config.yaml` contains `default-runtime: nvidia`
- Restart k3s-agent on GPU nodes
- Test GPU access: Deploy test pod with `nvidia.com/gpu: 1` resource request

## Migration from k3s_airgap Role

This role consolidates functionality from the deprecated `k3s_airgap` role and the `k3s.yaml` playbook. If you were previously using:

**Old approach:**
```yaml
- name: Prepare air-gapped k3s
  hosts: k3s_cluster
  tasks:
    - include_role:
        name: k3s_airgap
      when: air_gapped | bool

- name: Install k3s
  hosts: server
  tasks:
    # inline k3s installation tasks...
```

**New approach:**
```yaml
- name: Install k3s
  hosts: k3s_cluster
  roles:
    - k3s
```

All variables remain the same. No inventory changes required.

## License

MIT

## Author Information

Washington University School of Medicine - Translational Analytics Group (TAG)
