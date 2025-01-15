# Jaeger Deployment

The values.yaml file in this directory contains the configuration for the [Jaeger Helm Chart](https://github.com/jaegertracing/helm-charts/tree/main/charts/jaeger).

## Installation

First, add the Jaeger Helm repository:

```bash
helm repo add jaegertracing https://jaegertracing.github.io/helm-charts
helm repo update
```

Create the Jaeger namespace:

```bash
kubectl create namespace jaeger
```

Create directory for Jaeger / Elasticsearch data (this only needs to be done once):

```bash
sudo mkdir -p /ceph/tag/home/jaeger/elasticsearch
sudo chmod +777 /ceph/tag/home/jaeger
sudo chmod +777 /ceph/tag/home/jaeger/elasticsearch
```

Create the Persistent Volume and Persistent Volume Claim for Jaeger / Elasticsearch (this only needs to be done once):

```bash
kubectl apply -f jaeger-elasticsearch-pv.yaml -n jaeger
kubectl apply -f jaeger-elasticsearch-pvc.yaml -n jaeger
```

Install Jaeger with:

```bash
helm upgrade --install jaeger jaegertracing/jaeger --namespace jaeger --version 3.3.3 --values values.yaml
```

To uninstall Jaeger, run:

```bash
helm uninstall jaeger --namespace jaeger
```

## Accessing Jaeger

To access Jaeger, run:

```bash
kubectl port-forward svc/jaeger-query 16685:80 -n jaeger
```

## Storage backend for Jaeger

Jaeger supports multiple storage backends. The default storage backend is [*currently*](https://github.com/jaegertracing/helm-charts/tree/main/charts/jaeger#storage) Cassandra, but Elasticsearch or OpenSearch is [recommended](https://www.jaegertracing.io/docs/2.2/faq/#what-is-the-recommended-storage-backend) by the Jaeger team over Cassandra. The default backend for Jaeger could change in the future. 