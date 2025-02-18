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

A few values need to be set in the `hub-values.yaml`, `auth-values.yaml`, and `jupyterhub-ingress.yaml` files before installing JupyterHub.

- `FQDN`: The fully qualified domain name for the JupyterHub instance. 
- `JUPYTERHUB_GITHUB_CLIENT_ID`: The GitHub OAuth client ID.
- `JUPYTERHUB_GITHUB_CLIENT_SECRET`: The GitHub OAuth client secret.
- `JUPYTERHUB_GITHUB_ALLOWED_ORGS`: Users from these GitHub organizations are allowed to log in.

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

