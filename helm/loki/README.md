# Loki Deployment

The values.yaml file in this directory contains the configuration for the [grafana/loki](https://github.com/grafana/loki/tree/main/production/helm/loki) Helm chart.

Loki is deployed via Ansible (see `ansible/roles/loki/` and `make install-monitor`). Pod logs are shipped to Loki by Grafana Alloy (see `ansible/roles/alloy/`).

## Accessing Loki

Loki can be accessed through Grafana.

## Loki Configuration

Loki is deployed in `SingleBinary` mode, which is recommended for small scale deployments. There is currently a replication factor of 1 for the Loki instance. A second node would need to be added to the cluster and the replication factor increased for high availability.
