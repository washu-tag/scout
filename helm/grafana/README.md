# Grafana Deployment

The values.yaml file in this directory contains the configuration for the [Grafana Helm Chart](https://github.com/grafana/helm-charts/tree/main/charts/grafana) .

## Installation

First, add the Grafana Helm repository:

```bash
helm repo add grafana https://grafana.github.io/helm-charts
helm repo update
```

Create the Grafana namespace:

```bash
kubectl create namespace grafana
```

Create directory for Grafana data (this only needs to be done once):

```bash
sudo mkdir /ceph/tag/home/grafana
sudo chmod +777 /ceph/tag/home/grafana
```

Create the Persistent Volume and Persistent Volume Claim for Grafana (this only needs to be done once):

```bash
kubectl apply -f grafana-pv.yaml -n grafana
kubectl apply -f grafana-pvc.yaml -n grafana
```

(Optional) To build the dashboard configmaps and output to stdout:

```bash
kubectl kustomize .
```

Build and deploy the dashboard configmaps:

```bash
kubectl apply -k . -n grafana
```

Install Grafana with:

```bash
helm upgrade --install grafana grafana/grafana --namespace grafana --version 8.8.2 --values values.yaml
```

To uninstall Grafana, run:

```bash
helm uninstall grafana --namespace grafana
```

## Accessing Grafana

To access Grafana, run:

```bash
kubectl port-forward svc/grafana 3000:80 -n grafana
```

The login page is disabled and allows anonymous access with the default role of Admin.

Any changes to the Grafana configuration can be made in the `values.yaml` file. The Grafana UI can also be used to make changes to the configuration but we will ne

## Working with Dashboards

Dashboards are stored as JSON files and can be managed with Git. Unfortunately, the Grafana UI doesn't support Git integration. Grafana is setup to persist between restarts, so you won't lose your dashboards if the pod is restarted. But some core dashboards we will want to manage with Git. To do this, dashboards are developed in the Grafana UI, exported as JSON, and then stored in the `dashboards` directory. The `kustomization.yaml` file will build the ConfigMap for the dashboards and the Grafana sidecar will load the dashboards into Grafana. To save or update a dashboard in our Git repository, export the dashboard as JSON and save it in the `dashboards` directory. If it's a new dashboard, update the `kustomization.yaml` file to include the new dashboard. The redeploy the dashboard config maps with `kubectl apply -k . -n grafana`.

## Dashboard Sources

The 
- [Jaeger Dashboard](https://github.com/jaegertracing/jaeger/blob/main/monitoring/jaeger-mixin/dashboard-for-grafana.json)
