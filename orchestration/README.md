# Orchestration

## Install Temporal
Deploy helm chart to kubernetes.
```
helm repo add temporal https://go.temporal.io/helm-charts
helm repo update temporal
kubectl create ns temporal
helm upgrade --install -n temporal temporal temporal/temporal
```
