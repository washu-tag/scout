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
| `telepresence_helm_values` | node agent on, agent injector off | Chart values, passed through unchanged; an override replaces the whole default |
| `telepresence_allow_protected_workloads` | `false` | Apply the NetworkPolicy allowances below; `false` removes them |

## Network policies

Trino and report-viewer have ingress NetworkPolicies that block traffic from the traffic
manager's namespace, so intercepts and connections to them fail. With
`telepresence_allow_protected_workloads: true`, the role applies
`templates/allow-telepresence.yaml.j2`, which adds an allowance from that namespace on
each workload's serving port. NetworkPolicies are additive, so these open no other path.
To cover another protected workload, add a policy to the template whose `podSelector`
matches that workload's existing policy.

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

`telepresence_allow_protected_workloads` adds the traffic manager's namespace to the pods
allowed to connect to Trino and report-viewer. Both still require a valid token, but this
removes the network restriction that otherwise limits Trino to its own namespace, so a
stolen token used from that namespace is no longer blocked at the network layer. Enable it
only on clusters without production data.

## Uninstalling

Setting the flag back to false does not remove the traffic manager or the network
policies. Remove the policies first, with `telepresence_allow_protected_workloads: false`
and `make install-telepresence` or with
`kubectl delete networkpolicy -A -l app.kubernetes.io/part-of=telepresence-dev-access`,
then remove the traffic manager with `helm uninstall traffic-manager -n ambassador` or
`telepresence helm uninstall`.
