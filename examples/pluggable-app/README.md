# Example Pluggable App

A complete, installable Scout **pluggable app** in one Helm chart: an app that arrives with
its own landing-page presence and its own Keycloak client, and that leaves nothing behind
when you uninstall it. No edit to the base realm, no Ansible variable, no change to any
Scout component.

The service inside is deliberately trivial — one page, standard library only, no
dependencies and no image to build. The chart around it is the part worth copying.

Two audiences:

- **Writing a pluggable app.** Read the two contracts below, then copy the chart and
  replace `files/app.py` with your image.
- **Working on Scout.** Install it as a scaffold when you need a real service behind the
  edge gate with a real Keycloak client, then tear it down.

It is not a production component. Nothing deploys it, no CI job builds it, and it is
absent from `deploy/`.

## What makes it pluggable

Exactly two ConfigMaps, each discovered by a label rather than by being registered
anywhere:

| File                                      | Contract                                   | What it does                                                                      |
| ----------------------------------------- | ------------------------------------------ | --------------------------------------------------------------------------------- |
| `templates/launchpad-catalog.yaml`        | `launchpad.scout.xnat.org/catalog: "true"` | Puts a chip on the launchpad (ADR 0034)                                           |
| `templates/keycloak-fragment.yaml`        | `keycloak.scout.xnat.org/fragment: "true"` | Creates the app's Keycloak client and roles (ADR 0037)                            |
| `templates/keycloak-client-secret.yaml`   | —                                          | The client credential, which the app owns and the fragment only points at         |
| `templates/keycloak-reconciler-rbac.yaml` | —                                          | Lets the reconciler read that one Secret. **The object authors forget**           |
| `templates/ingress.yaml`                  | —                                          | Puts the app behind the oauth2-proxy edge gate via Traefik middleware annotations |

The rest — `deployment.yaml`, `service.yaml`, `app-configmap.yaml`, `files/app.py` — is an
ordinary service, and is what you replace.

The two contracts are independent. A back-end service with no UI ships only the fragment; a
link to something outside the cluster ships only the chip.

### The chip

The launchpad watches for labelled ConfigMaps in any namespace and picks one up within
about ten seconds. Presentation fields fail soft: an `icon` or `tone` outside the
documented sets falls back to the default rather than failing your install. `id`, `title`,
and `link` are required, and a chip missing one is skipped.

Fields, valid icons, and valid tones: `docs/source/customize/launchpad-chips.md`.

### The fragment

The fragment declares Scout concepts, not Keycloak objects. It is deliberately much
narrower than Keycloak: there is no syntax for protocol mappers, client scopes, service
accounts, the login flow, PKCE, or `fullScopeAllowed`. Those are Scout's to decide, and
naming one is a rejection rather than a warning — unknown fields fail the whole document.

The interesting field is `grants`:

```yaml
roles:
  - example-app-user
  - example-app-admin
grants:
  scout-user: [example-app-user]
  scout-admin: [example-app-admin]
```

That says _a Scout user gets my user role; a Scout admin gets my admin role_. The app
defines its own role vocabulary and maps Scout's two tiers onto it; it never enumerates
people. `grants` may only name roles the fragment itself declares, and may only target
`scout-user` and `scout-admin`.

URLs are checked: `appUrl` and every entry in `redirectUris` must be `https` and under the
site's own domain, with no wildcards.

### The credential, and the grant that makes it readable

The fragment carries a **reference** to a Secret, never a secret. The app creates the
Secret in its own namespace, and `templates/keycloak-reconciler-rbac.yaml` grants the
reconciler `get` on that one name.

Leave the RBAC out and the client is silently never created. Look for a `FragmentFailed`
event on the fragment ConfigMap.

`reconcilerServiceAccount` is stable contract. `reconcilerNamespace` follows the site's
`keycloak_namespace`, so override it if your site moved Keycloak.

## Install

`domain` is the only required value.

```bash
helm install example-app examples/pluggable-app \
  --namespace scout-example --create-namespace \
  --set domain=your-scout-domain.org
```

The app is then at `https://example-app.<domain>`, and a chip for it appears on the
launchpad.

## Check that it worked

The page itself reports both halves: the username the edge gate forwarded, and whether the
client credential is mounted. For the parts the page cannot see:

```bash
# The fragment was accepted (look for FragmentApplied; FragmentInvalid,
# FragmentRejected and FragmentFailed each say why not)
kubectl describe cm -n scout-example example-pluggable-app-keycloak

# What the reconciler made of it
kubectl logs -n scout-core deploy/scout-keycloak-fragment-reconciler
```

Then in the Keycloak admin console: the client `example-app` exists with roles
`example-app-user` and `example-app-admin`, and the `scout-user` / `scout-admin` realm
roles now list those as composites.

## Uninstall

```bash
helm uninstall example-app --namespace scout-example
```

The chip disappears within seconds. The client is garbage-collected on a later reconcile
pass, after a grace period — the reconciler knows the client is its own because it recorded
ownership as an attribute on the client, and it will not touch anything it did not create.

## Adapting it

**As a scaffold.** Change `subdomain` and `clientId` so you do not collide with anything
real, edit `files/app.py`, and reinstall. The pod restarts on its own when the file
changes.

**As a template for a real app.** Rename it with the two values `clientId` and `subdomain`
(the chip id, the Secret name and the RBAC grant all follow `clientId`), then:

1. Point `image.repository` and `image.tag` at your build, and drop `command`.
2. Delete `templates/app-configmap.yaml`, the `app` volume and its mount, and the
   `checksum/app` annotation.
3. Delete `files/`.

Keep the fragment, the chip, the Secret, the RBAC, and the ingress annotations.

## What this example leaves out, on purpose

- **A login flow.** The app never exchanges its credential for a token, so the roles the
  fragment declares are visible in Keycloak but nothing reads them yet. The
  `redirectUris` entry points at a `/auth/callback` this app does not serve; it is there
  because it is the field you will need. An app that does log users in runs a standard
  authorization-code flow against its own client and reads its roles from the `groups`
  claim (override with `roleClaim`).

- **A NetworkPolicy.** The page trusts `X-Auth-Request-Preferred-Username`, which is only
  sound because the edge gate is the sole route to the pod. In production that assumption
  has to be enforced, not assumed — anything that can reach the Service directly can forge
  the header. See `helm/voila/templates/networkpolicy.yaml` for the pattern, and ADR 0022.

- **An image build.** Running the app from a ConfigMap on a stock `python:3.12-slim` keeps
  the chart self-contained and installable with nothing built first. Real apps ship an
  image; see `.github/workflows/ci.yaml` for what wiring one up involves.

## Further reading

- ADR 0034 — launchpad catalog: how chips are discovered
- ADR 0037 — Keycloak realm fragments: why the reconciler and `keycloak-config-cli` are
  disjoint writers, and what the fragment vocabulary deliberately cannot say
- ADR 0022 — Trino auth and the edge-forwarded identity header
- `docs/source/customize/launchpad-chips.md` — the chip field reference
