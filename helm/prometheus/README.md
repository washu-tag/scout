# Prometheus Deployment

The values.yaml file in this directory contains the configuration for the [prometheus-community/prometheus][prometheus-community-prometheus] Helm chart. 

## Installation

First, add the Prometheus Helm repository:

```bash
helm repo add prometheus-community https://prometheus-community.github.io/helm-charts
helm repo update
```

Create the Prometheus namespace:

```bash
kubectl create namespace prometheus
```

Create directory for Prometheus data (this only needs to be done once):

```bash
sudo mkdir /ceph/tag/home/prometheus
sudo chmod +777 /ceph/tag/home/prometheus
```

Create the Persistent Volume and Persistent Volume Claim for Prometheus (this only needs to be done once):

```bash
kubectl apply -f prometheus-pv.yaml -n prometheus
kubectl apply -f prometheus-pvc.yaml -n prometheus
```

Install Prometheus with:

```bash
helm upgrade --install prometheus prometheus-community/prometheus --namespace prometheus --version 26.1.0 --values values.yaml
```

To uninstall Prometheus, run:

```bash
helm uninstall prometheus -n prometheus
```

## Accessing Prometheus

To access Prometheus directly, run:

```bash
kubectl port-forward svc/prometheus-server 9090:9090 -n prometheus
```

Prometheus can also be accessed through Grafana.