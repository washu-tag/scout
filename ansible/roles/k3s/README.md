# k3s Role

This role installs and configures [k3s](https://k3s.io/) - a lightweight Kubernetes distribution - on target nodes. It supports both online (internet-connected) and air-gapped (offline) deployment modes.

## Role Variables

See [Creating the Ansible Inventory File](../../../docs/source/technical/inventory.md#k3s) and [Air-Gapped Deployment](../../../docs/source/technical/air-gapped.md#air-gapped-configuration-variables) for user-configurable variables.

**Key variables for developers:**
- `k3s_artifact_temp_dir`: Computed at runtime - temporary directory on control node for artifacts
- `k3s_install_script_path`: Computed as `{{ k3s_bin_dir }}/get.k3s.io.sh`
- `use_staging_node`: Internal flag set when air-gapped mode is enabled and staging host exists
- `staging_harbor_host`: Computed from staging node Harbor configuration
- `registry_config_changed`: Tracks whether registry mirrors have changed (triggers k3s restart)

See `defaults/main.yaml` for complete variable definitions and defaults.

When Ansible runs outside the cluster, `k3s_api_host` selects the API hostname in
the local kubeconfig. It defaults to `ansible_host` or `inventory_hostname`.
The role maintains these certificate names in a native K3s configuration drop-in
on new and existing installations, preserving other SANs and restarting an
existing server only when configuration changes. It verifies the external API
with a private candidate kubeconfig before replacing the local copy. Failed DNS,
connectivity or certificate checks leave the existing local kubeconfig intact.

The kubeconfig embeds the K3s CA, so `kubectl` needs no additional laptop trust
store configuration. Copied client certificates must be refreshed when K3s renews
them. The role uses the default `/etc/rancher/k3s/config.yaml` configuration path;
installations using `--config` or `K3S_CONFIG_FILE` must include the required
`tls-san` names in their custom configuration or corresponding drop-in directory.

When staging registry mirrors are enabled, both servers and agents disable
containerd's default registry endpoint fallback. Registries without a configured
mirror can still use their default endpoint.

## Dependencies

- `scout_common` role (for Helm chart deployment patterns)
- `harbor` role variables (for air-gapped registry mirror configuration)

## Usage

See [Creating the Ansible Inventory File](../../../docs/source/technical/inventory.md) and [Air-Gapped Deployment](../../../docs/source/technical/air-gapped.md) for complete configuration examples and deployment instructions.

## Task Organization

The role is organized into focused task files:

- `main.yaml`: Orchestration - coordinates all installation steps
- `prepare_k3s_binaries.yaml`: Prepares k3s binaries and install script (online and air-gapped modes)
- `selinux.yaml`: SELinux auto-detection and package installation
- `registry.yaml`: Harbor registry mirror configuration
- `server.yaml`: k3s server (control plane) installation
- `coredns.yaml`: CoreDNS customization (deny-all for air-gapped, domain forwarding, custom server blocks)
- `agent.yaml`: k3s agent (worker) installation
- `gpu.yaml`: GPU worker configuration

**Templates:**
- `templates/coredns-custom-data.yaml.j2`: Generates the `data:` section for the `coredns-custom` ConfigMap

## CoreDNS Customization

The role manages a `coredns-custom` ConfigMap in `kube-system` that overrides CoreDNS behavior. This uses a three-layer configuration model:

### Layer 1: Air-Gap Deny-All (automatic)

When `air_gapped: true`, the role automatically creates:
- A deny-all override that returns NXDOMAIN for all unknown domains (prevents DNS flooding of upstream resolvers like Tailscale MagicDNS)
- Server blocks for `cluster.local` and reverse DNS so internal Kubernetes resolution continues to work

### Layer 2: Domain Forwarding

- **`coredns_forward_map`**: Map of forwarding destinations to domain lists. Each entry creates a CoreDNS server block forwarding the listed domains to the specified destination.

```yaml
coredns_forward_map:
  /etc/resolv.conf:
    - wustl.edu
  100.100.100.100:
    - ts.net
```

> **Deprecated:** `coredns_forward_domains` (list) is deprecated. It is automatically converted to a single `/etc/resolv.conf` entry in the forward map. Setting both variables is an error.

### Layer 3: Arbitrary Server Blocks

`coredns_extra_server_blocks` is a dict of name -> raw Corefile content for full flexibility. Works independently of air-gapped mode. Keys become ConfigMap data keys with `.server` suffix auto-appended.

```yaml
coredns_extra_server_blocks:
  scout-override: !unsafe |
    app.example.com:53 {
      template IN A app.example.com {
        answer "{{ .Name }} 60 IN A 198.51.100.10"
      }
    }
```

**Important:** Values containing Go template syntax (e.g., `{{ .Name }}`) must use the `!unsafe` YAML tag to prevent Ansible from interpreting them as Jinja2 expressions.

### Behavior

- Only runs on server nodes (not staging)
- When no customization is needed, the ConfigMap is removed (idempotent)
- CoreDNS is restarted only when the ConfigMap actually changes

## Testing

This role includes Molecule integration tests that perform actual k3s installation in Docker containers:

```bash
cd ansible/roles/k3s
# Requires Docker and molecule-plugins[docker]
uvx --with molecule-plugins[docker] molecule test -s integration
```

**What the tests do:**
- Spin up Rocky Linux 9 container with systemd support
- Install k3s server using the role
- Verify k3s systemd service is active
- Verify k3s cluster is responsive (`kubectl get nodes`)
- Verify node reaches Ready state
- Check kubeconfig file generation and permissions
- Install a baseline cluster and confirm its certificate lacks the requested inventory name
- Re-run the role to add the name while preserving existing YAML and CLI SANs, the CA and a cluster identity marker
- Verify the external kubeconfig passes API verification and has private permissions

**Why integration tests (not unit tests):**
The k3s role performs system-level operations (systemd service management, privileged installation, Kubernetes cluster creation) that cannot be meaningfully mocked. Integration tests validate real installation behavior including systemd integration and cluster health.

**Limitations:**
- Tests require Docker daemon with privileged container support
- Air-gapped mode testing requires pre-staged artifacts (not yet implemented)
- Multi-node scenarios are tested in CI with real deployments
- Tests take 2-5 minutes (vs seconds for unit tests)
