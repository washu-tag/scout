# MailHog Deployment

This configuration deploys MailHog as a service and a deployment in a Kubernetes cluster. It is useful for testing email functionality in applications.

## Components

### Service

The `mailhog-service` exposes MailHog on the following ports:
- **SMTP**: Port `1025`
- **HTTP**: Port `8025`

## Usage

Create a namespace for MailHog:

```sh
kubectl create namespace mailhog
```

To deploy MailHog, apply the configuration using `kubectl`:

```sh
kubectl apply -f mailhog.yaml -n mailhog
```

This will create the MailHog service and deployment in your Kubernetes cluster. You can access the MailHog web interface at `http://<your-cluster-ip>:8025` after port-forwarding the service with:

```sh
kubectl port-forward svc/mailhog-service 8025:8025 -n mailhog
```