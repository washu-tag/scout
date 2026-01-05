# Loki Deployment

The values.yaml file in this directory contains the configuration for the [grafana/loki](https://github.com/grafana/loki/tree/main/production/helm/loki) Helm chart.

## Installation

Add the Grafana Loki Helm repository:

```bash
helm repo add grafana https://grafana.github.io/helm-charts
helm repo update
```

Create the Loki namespace:

```bash
kubectl create namespace loki
```

Loki dependes on Minio for storage. In an existing Minio deployment, login to the Minio console and create the following buckets:

- loki-chunks
- loki-ruler
- loki-admin

Next, create access key and secret key for Loki in the Minio console and record them.

Create the k8s secret for Loki with the access key and secret key:

```bash
kubectl create secret generic loki-secrets -n loki \
--from-literal='AWS_ACCESS_KEY_ID=*****' \
--from-literal='AWS_SECRET_ACCESS_KEY=*****'
```

Install Loki with:

```bash
helm upgrade --install loki grafana/loki --namespace loki --version 6.24.0 --values loki-values.yaml
```

Grafana Alloy is used to collect logs from the Kubernetes cluster and send them to Loki. Install Alloy with:

```bash
helm upgrade --install alloy grafana/alloy --namespace loki --version 1.5.1 --values alloy-values.yaml
```

Uninstall Loki with:

```bash
helm uninstall loki --namespace loki
```

Uninstall Alloy with:

```bash
helm uninstall alloy --namespace loki
```

## Accessing Loki

Loki can be accessed through Grafana.

## Loki Configuration

Loki is deployed in `SingleBinary` mode, which is recommended for small scale deployments. There is currently a replication factor of 1 for the Loki instance. A second node would need to be added to the cluster and the replication factor increased for high availability. 
