# JupyterHub Deployment

The values.yaml file in this directory contains the configuration for the [jupyterhub/jupyterhub](https://hub.jupyter.org/helm-chart/) Helm chart.

## Installation

First, add the JupyterHub Helm repository:

```bash
helm repo add jupyterhub https://jupyterhub.github.io/helm-chart
helm repo update
```

Create the JupyterHub namespace:

```bash
kubectl create namespace jupyter
```

Install JupyterHub with:

```bash
helm upgrade --install jupyter jupyterhub/jupyterhub --namespace jupyter --version 4.1.0 --values hub-values.yaml --values auth-values.yaml
```

We need to patch the ingress due to a hard-coded trailing slash in the subpath.

```bash
kubectl -n jupyter patch ingress jupyterhub --type strategic --patch-file jupyterhub-ingress.yaml
```

To uninstall JupyterHub, run:

```bash
helm uninstall jupyterhub -n jupyter
```

