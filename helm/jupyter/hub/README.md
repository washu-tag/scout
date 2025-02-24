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

Helm doesn't support variable expansion within charts, so users will need to manually replace these variables in the 
values.yaml files or use the Ansible playbooks to install.

- `FQDN`: The fully qualified domain name for the JupyterHub instance. 
- `JUPYTERHUB_GITHUB_CLIENT_ID`: The GitHub OAuth client ID.
- `JUPYTERHUB_GITHUB_CLIENT_SECRET`: The GitHub OAuth client secret.
- `JUPYTERHUB_GITHUB_ALLOWED_ORG`: Users from this GitHub organization will be allowed to log in. Only one organization is supported.
- `JUPYTERHUB_METRICS_API_TOKEN`: The token for accessing the JupyterHub metrics endpoint.

Install the metrics-api-token as a secret in the `prometheus` namespace:

```bash
kubectl create secret generic jupyterhub-metrics-api-token --from-file=metrics-api-token -n prometheus 
```

Install JupyterHub with:

```bash
helm upgrade --install jupyter jupyterhub/jupyterhub --namespace jupyter --version 4.1.0 --values hub-values.yaml --values auth-values.yaml
```

To uninstall JupyterHub, run:

```bash
helm uninstall jupyterhub -n jupyter
```

