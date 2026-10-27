# telepresence

Installs the [Telepresence](https://telepresence.io) traffic manager (OSS Helm chart from
`oci://ghcr.io/telepresenceio/telepresence-oss`) so developers can connect a local
workstation to the cluster network and intercept workloads.

## Enabling

Off by default. In `inventory.yaml`:

```yaml
enable_telepresence: true
```

Then `make install-telepresence` (or `make all`). With the flag unset or false, the
playbook is a no-op.

## Variables

| Variable | Default | Purpose |
| --- | --- | --- |
| `telepresence_chart_version` | `versions.yaml` | Chart version (Renovate-monitored) |
| `telepresence_namespace` | `ambassador` | Traffic manager namespace; the CLI's default |
| `telepresence_helm_release_name` | `traffic-manager` | Helm release name |
| `telepresence_helm_timeout` | `5m` | Helm wait timeout |
| `telepresence_helm_values` | `{}` | Extra chart values, passed through unchanged |

## Connecting

With a kubeconfig for the cluster and a CLI version matching the chart:

```bash
telepresence connect
```

If `telepresence_namespace` is changed, pass `--manager-namespace <ns>` to `connect`.

## Security

The traffic manager can intercept traffic for any workload it is allowed to reach,
and that traffic includes PHI. With default values, it is allowed to reach every
namespace in the cluster. To limit it to specific namespaces, set chart values in
`telepresence_helm_values`. Only enable it where developers already have cluster
access.

## Uninstalling

Setting the flag back to false does not remove the traffic manager. Remove it with
`helm uninstall traffic-manager -n ambassador` or `telepresence helm uninstall`.
