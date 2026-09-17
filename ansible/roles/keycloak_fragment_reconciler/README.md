# keycloak_fragment_reconciler

Deploys the Keycloak realm fragment reconciler (ADR 0037): the service that turns a
pluggable app's labelled ConfigMap into a Keycloak client, and removes the client again
when the app goes away.

Runs from `playbooks/auth.yaml`, after `keycloak` because it needs the base realm's tier
roles, and after `oauth2-proxy` because it waits on readiness.

## Required variables

| Variable | Notes |
| --- | --- |
| `keycloak_fragment_reconciler_svc_client_secret` | The credential for the realm's `fragment_reconciler_svc` principal. Set it in `inventory.yaml`, vault-encrypted; the `keycloak` role writes it into the `keycloak-client-secrets` Secret, and this role checks it arrived. |

Everything else has a default in `defaults/main.yaml`.

## Overriding the image

`keycloak_fragment_reconciler_image_repository` defaults to the published
`ghcr.io/washu-tag/keycloak-fragment-reconciler`. A site running a locally built image
overrides the repository and tag, and should set
`keycloak_fragment_reconciler_image_pull_policy: Always` if the tag is mutable — otherwise
nodes keep serving whichever build they cached first.

A missing image surfaces as a Helm timeout rather than a pull error. Check with
`kubectl describe pod -n scout-core -l app.kubernetes.io/name=keycloak-fragment-reconciler`.

## Readiness

`/readyz` returns 200 only when every configured tier realm role exists and Keycloak is
reachable, which makes `helm_chart_wait` here a real gate on the realm apply having landed.
`/healthz` is the liveness probe and asks only whether the process is up, so a Keycloak
outage does not crashloop the pod.

## What it owns

One replica, `strategy: Recreate`, no leader election or autoscaling — hardcoded in the
chart rather than exposed as values, because two reconcilers writing the same realm objects
is the failure the service is designed against and its orphan grace clock assumes one
process.

It writes only clients carrying its own ownership attribute, their roles, their protocol
mappers, and composite edges naming those roles. It holds no standing Secret permission:
each app's chart grants it a `resourceNames`-scoped `get` on that app's own credential.
**The ServiceAccount name is public contract** — apps name it in a `RoleBinding`, so
renaming it breaks every installed fragment, and it is deliberately not derived from the
Helm release name. Apps name the namespace too, but it follows `keycloak_namespace`, so a
site that overrides namespaces has to use its own. See
`docs/source/customize/keycloak-fragments.md`.
