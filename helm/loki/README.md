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

Create directory for Loki data (this only needs to be done once):

```bash
sudo mkdir /ceph/tag/home/loki
sudo chmod +777 /ceph/tag/home/loki
```

Create the Persistent Volume and Persistent Volume Claim for Loki (this only needs to be done once):

```bash
kubectl apply -f loki-pv.yaml -n loki
kubectl apply -f loki-pvc.yaml -n loki
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

Promtail agent is used to collect logs from the Kubernetes cluster and send them to Loki. Install Promtail with:

```bash
helm upgrade --install promtail grafana/promtail --namespace loki --version 6.16.6 --values promtail-values.yaml
```

Uninstall Loki with:

```bash
helm uninstall loki --namespace loki
```

Uninstall Promtail with:

```bash
helm uninstall promtail --namespace loki
```

## Accessing Loki

Loki can be accessed through Grafana.

## Loki Configuration

Loki is deployed in `SingleBinary` mode, which is recommended for small scale deployments. There is currently a replication factor of 1 for the Loki instance. A second node would need to be added to the cluster and the replication factor increased for high availability. 