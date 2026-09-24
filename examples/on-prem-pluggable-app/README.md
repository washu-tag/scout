# Example Pluggable App

This code serves as a reference implementation for a Scout Pluggable App. It is a Helm chart that deploys a dummy service. The chart is where all the components live that make this a pluggable app. Those are the parts you'll need to copy and modify to create your own pluggable app.

This chart and the app is intentionally not installed in any production Scout. It can be deployed as a development aid, but its primary purpose is as a reference for pluggable app authors.

## On-prem only

This chart assumes a Scout deployed in on-prem mode, where Traefik is the ingress controller and OAuth2 Proxy gates every request through Traefik forwardAuth Middlewares. Three parts of the chart depend on that:

- The Middleware annotations in `values.yaml`, which put the app behind OAuth2 Proxy.
- The `X-Auth-Request-Preferred-Username` header `files/app.py` reads, which OAuth2 Proxy sets.
- `templates/networkpolicy.yaml`, which admits traffic only from Traefik.

In aws mode, Scout's services sit behind AWS ALB Ingresses using ALB-native OIDC instead. None of those Middlewares exist and no identity header is set, so this chart's Ingress will not work there. The launchpad chip, Keycloak fragment, client Secret, and RBAC do not depend on the edge and work the same in either mode.

## Pluggable App components

All the components that make a Helm chart into a Pluggable App. For more on each of these, see the docs on [Customizing and Extending Scout](https://washu-scout.readthedocs.io/en/latest/customize/index.html).


| File | Label | What it does |
| -- | -- | -- |
| `templates/launchpad-catalog.yaml`        | `launchpad.scout.xnat.org/catalog: "true"` | Puts a chip on the launchpad |
| `templates/keycloak-fragment.yaml`        | `keycloak.scout.xnat.org/fragment: "true"` | Creates the app's Keycloak client and roles |
| `templates/keycloak-client-secret.yaml`   | — | The Keycloak client's secret credential |
| `templates/keycloak-reconciler-rbac.yaml` | — | Gives the reconciler service permission to read the Secret |
| `templates/ingress.yaml`                  | — | Puts the app behind the oauth2-proxy edge gate via Traefik middleware annotations |

The rest of the stuff in the chart—`deployment.yaml`, `service.yaml`, `app-configmap.yaml`, `files/app.py`—is more or less an ordinary service, with the caveat that it's a service that isn't built into a docker image and it doesn't do anything interesting.

### The Launchpad chip

Launchpad watches for ConfigMaps labelled with `launchpad.scout.xnat.org/catalog: "true"`, picks them up, and uses their contents to populate a Launchpad "chip" UI element. The contents of the ConfigMap need to be a `Catalog` following the instructions in [Launchpad Chips](https://washu-scout.readthedocs.io/en/latest/customize/launchpad-chips.html)

### The Keycloak client fragment

The Reconciler watches for ConfigMaps labelled with `keycloak.scout.xnat.org/fragment: "true"`, picks them up, and uses their contents to create a client and roles in Scout's Keycloak realm. The contents of the ConfigMap need to be a `Fragment` following the instructions in [Authenticate Users to Your Service](https://washu-scout.readthedocs.io/en/latest/customize/service-authentication.html).

### The Keycloak client secret + RBAC grant

The `Fragment` in the ConfigMap holds the name of a Secret holding the Keycloak client's secret key. Each Pluggable App creates that Secret in its own namespace, and `templates/keycloak-reconciler-rbac.yaml` grants the Reconciler service RBAC permissions to `get` that particular Secret.

The purpose of this is to keep the Reconciler's permissions scoped to only exactly those Secrets it needs to read. We don't want it to be able to read every Secret for every service in the whole platform. But it does need to be able to read the specific Secrets for the specific Keycloak clients for Pluggable Apps. So authors of those apps need to include the RBAC which grants those permissions to the Reconciler.

## Install

`domain` is the only required value.

```bash
helm install example-app examples/on-prem-pluggable-app \
  --namespace scout-example --create-namespace \
  --set domain=your-scout-domain.org
```

The app is then at `https://example-app.<domain>`, and a chip for it appears on the Launchpad.

To verify the installation worked:

```bash
# The fragment was accepted (look for FragmentApplied; FragmentInvalid,
# FragmentRejected and FragmentFailed each say why not)
kubectl describe cm -n scout-example example-pluggable-app-keycloak

# Inspect the reconciler's logs to see if the fragment was received, 
# read, and applied, or if an error occurred
kubectl logs -n scout-core deploy/keycloak-fragment-reconciler
```

## Uninstall

```bash
helm uninstall example-app --namespace scout-example
```

## Adapting it

### As a dev scaffold
Change `subdomain` and `clientId` so you do not collide with anything real, edit `files/app.py`, and reinstall. The pod restarts on its own when the file changes.

### As a template for your own Pluggable App

1. Point `image.repository` and `image.tag` at your service's image, and drop `command`.
2. Delete `templates/app-configmap.yaml`, the `app` volume and its mount, and the
   `checksum/app` annotation. Those were only there to support the basic `app.py` service.
3. Delete `files/`.

Keep the fragment, the chip, the Secret, the RBAC, and the ingress annotations. Use the docs to [customize your app](https://washu-scout.readthedocs.io/en/latest/customize/index.html).
