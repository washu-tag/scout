# Ship a Keycloak Client with Your App

```{note}
**Draft.** The fragment vocabulary is still moving — treat field names here as
provisional. Working notes: `docs/internal/keycloak-fragments-plan.md`.
```

An app that needs its own Keycloak client ships one. Publish a ConfigMap — in your app's
own namespace — labelled `keycloak.scout.xnat.org/fragment: "true"`, holding a
**fragment**: a short document declaring the client, its roles, and which Scout users get
them. The platform reconciler discovers it and composes it into the realm on its next
pass (see [Operating the App Manager](../operate/keycloak-fragments.md)). Uninstall the
chart and the client, its roles, and its grants go away with it.

A fragment declares Scout concepts, not Keycloak JSON. The security-relevant choices —
confidential client, which flows are off, scope handling, the role mapper, web origins,
the platform signout URI — belong to the platform and have no syntax in a fragment. If
you find yourself wanting a field that isn't listed below, that's a conversation about
the vocabulary, not something to work around.

## A minimal fragment

```yaml
apiVersion: v1
kind: ConfigMap
metadata:
  name: my-service-keycloak # convention: <component>-keycloak
  namespace: my-service
  labels:
    keycloak.scout.xnat.org/fragment: 'true'
data:
  fragment.yaml: |
    apiVersion: keycloak.scout.xnat.org/v1alpha1
    kind: KeycloakFragment
    clients:
      - clientId: my-service
        displayName: My Service
        loginFlows: [STANDARD]
        appUrl: https://my-service.${domain}
        redirectUris:
          - https://my-service.${domain}/auth/callback
        pkce: required
        roles:
          - my-service-user
          - my-service-admin
        roleClaim: groups
        grants:
          scout-user: [my-service-user]
          scout-admin: [my-service-admin]
        secretRef:
          name: my-service-keycloak-client
```

## Fields

| Field                 | Required | Default   | Notes                                                                                      |
| --------------------- | -------- | --------- | ------------------------------------------------------------------------------------------ |
| `clientId`            | yes      | —         | Must not already exist in the realm, or be declared by another fragment.                    |
| `secretRef`           | yes      | —         | `{name, key}` of the Secret holding the credential — see [below](#the-client-secret).        |
| `loginFlows`          | no       | `[STANDARD]` | `STANDARD` (browser login), `SERVICE_ACCOUNT`, `TOKEN_EXCHANGE`.                          |
| `redirectUris`        | for `STANDARD` | —   | Full `https` URLs inside the Scout domain. No wildcards.                                    |
| `appUrl`              | no       | `""`      | Where the app lives; becomes the client's root URL.                                         |
| `displayName`         | no       | `""`      | Shown in the Keycloak console and in the app manager's status output.                       |
| `description`         | no       | `""`      | Same.                                                                                       |
| `roles`               | no       | `[]`      | Your app's own role names — see [below](#your-roles). `STANDARD` clients only.               |
| `roleClaim`           | no       | `groups`  | The token claim your roles arrive in. `STANDARD` clients only.                               |
| `grants`              | no       | `{}`      | Which of your roles every `scout-user` / `scout-admin` gets.                                 |
| `pkce`                | no       | `off`     | `required` means Keycloak *demands* a code challenge — only set it if your client sends one. |
| `accessTokenLifespan` | no       | realm default | A duration like `900s`, `15m`, `8h`.                                                    |
| `sessionLifespan`     | no       | realm default | Same.                                                                                   |

Unknown fields are an error, not a warning: a fragment with a typo'd or unrecognised key
is excluded from the realm and reported.

## Where the values come from

### The domain

Never write a hostname. Use `${domain}` and the reconciler substitutes the site's domain
at compose time, so the same chart installs on any Scout site. It is the only placeholder
available; anything else fails validation.

### Your subdomain

Whatever host your Ingress serves is the host your redirect URIs must use — one value,
usually a chart value, rendered into both. The path (`/auth/callback` above) is whatever
your OIDC library uses; check its docs, don't guess. Every URI must be `https` and land
inside the Scout domain.

You do not declare the platform signout URI. It is added for you.

### Your roles

Nobody can tell you what roles your app has — you decide, and the fragment is where those
names become realm objects. They are *client* roles, so `admin` on your client is
unrelated to `admin` anywhere else in Scout; the prefixed style above
(`my-service-admin`) is convention, not a requirement. Your app reads them from the claim
named by `roleClaim`.

### Grants

`grants` is what makes your app usable on install: it maps your roles onto Scout's two
coarse tiers, `scout-user` and `scout-admin`. Nothing else is addressable, and you may
only grant roles your own client declares. It is also the line to think hardest about,
because `scout-user: [my-service-user]` means *every Scout user* gets that role the
moment your chart is installed.

### The client secret

The reconciler reads the credential from a Secret in **its own** namespace, not yours —
that's what keeps it from needing cluster-wide Secret read. So your chart renders the same
value into two namespaces: yours, for the app to mount, and the reconciler's, for the
realm apply.

The value never enters the composed realm. The reconciler writes
`$(env:fragment_<clientId>)` there and mounts your Secret into the import job, which is
what lets the composed realm be an ordinary ConfigMap. Two consequences for you:

- Rotating the Secret is enough. The reconciler notices the change and re-applies; you do
  not have to touch the fragment.
- A Secret that is missing or empty rejects the fragment rather than creating a client
  with a blank credential, so check the fragment's `errors` in the status document if a
  client does not appear.

## Where it goes in your chart

```
helm/my-service/templates/
  fragment.yaml        # the labelled ConfigMap above
  client-secret.yaml   # the credential, rendered into two namespaces
```

Both are ordinary templates, so the fragment's lifecycle is the chart's lifecycle. There
is no platform-side file to edit, no Ansible variable, and no entry in the realm
template.

## Check it before you deploy

The validator is the same code the reconciler runs, so it gives the same verdict without a
cluster. Render your chart and pipe it in — it picks the labelled ConfigMaps out of the
stream and ignores the rest of your manifests:

```console
$ helm template . | docker run -i --rm --entrypoint scout-app-manager \
    ghcr.io/washu-tag/scout-app-manager:latest validate - --domain scout.example.edu
OK       my-service/my-service-keycloak  (sha256:f8d95216755d)
    client my-service  [STANDARD]
        redirect  https://my-service.scout.example.edu/auth/callback
        redirect  https://auth.scout.example.edu/oauth2/sign_out
        origin    https://my-service.scout.example.edu
        secret    my-service-keycloak-client/client-secret -> $(env:fragment_my_service)
        pkce      required (S256)
        roles     my-service-user, my-service-admin (claim 'groups')
        grants    my-service-admin -> everyone in scout-admin
        grants    my-service-user -> everyone in scout-user
```

That is the whole effect your fragment has on the realm: the resolved URLs (including the
signout URI added for you), whether PKCE is enforced, the roles, and who the grants reach.
Read the last two lines before you ship — they are the ones that hand a role to every Scout
user.

It exits non-zero and prints the reasons instead if there is something wrong with the
fragment itself. Four of those are silent failures in a cluster, which is the argument for
running this first:

- the ConfigMap is missing the `keycloak.scout.xnat.org/fragment` label, so nothing ever
  discovers it;
- its data key does not end in `.yaml`, `.yml` or `.json`, so the reconciler reads nothing
  from it — or it carries a second dot, like `my.fragment.yaml`, which the discovery
  sidecar's filenames cannot tell apart from part of the ConfigMap's own name;
- a redirect URI points outside the Scout domain;
- a field name is misspelled — the vocabulary is closed, so a typo is an error, not a
  default.

**It checks your fragment on its own**, which is why you get the same answer wherever you
run it. What it cannot tell you is anything about the realm your fragment is going to land
in, so these five are the reconciler's to report and not this command's:

- your `clientId` is one the base realm already declares, or one Keycloak creates in every
  realm (`account`, `admin-cli`, `realm-management`, …);
- your `clientId` is one another fragment is also claiming — both are rejected, because
  "whoever reconciled first" is not something anyone can reason about at review time;
- your credential variable collides with a key the site's own `keycloak-client-secrets`
  defines;
- you grant into a group the base realm does not have;
- your `secretRef` points at a Secret that is missing or empty in the reconciler's
  namespace.

They show up as `rejected` in `scout-app-manager status` and in the reconciler's log, with
the reason, and they never affect any other fragment.

If you already have a Scout cluster, the reconciler's own pod carries the same binary and
`kubectl exec -i` pipes into it the same way. No image pull, and you are checking against
the version that site actually runs:

```console
$ helm template . | kubectl exec -i -n scout-core deploy/scout-app-manager -c reconciler \
    -- scout-app-manager validate - --domain scout.example.edu
```

`validate` also takes files and directories instead of `-`, and accepts a bare fragment
document as readily as the ConfigMap around it. `--domain` defaults to
`scout.example.edu`; pass your site's if a redirect URI's host matters to you. Add
`--signout-url` only if your site overrides the default
`https://auth.<domain>/oauth2/sign_out`.

## What you cannot declare

Protocol mappers, client scopes, `fullScopeAllowed`, service-account role assignments,
realm roles, groups other than the two grantable ones, another component's client, `http`
URLs, wildcards, off-domain redirect targets.

Nor `$(` anywhere in any field. The realm is imported with keycloak-config-cli's variable
substitution on, and substitution reads the whole document — `$(env:oauth2_proxy)` in a
display name would resolve a platform client's credential into a field you can read back.

## After you install

The reconciler is notified as soon as your ConfigMap lands, so it validates and composes
within a couple of seconds. An edit takes effect the same way.

Ask the reconciler what it decided about your fragment:

```console
$ kubectl get cm scout-app-manager-status -n scout-core -o yaml
```

Your fragment appears under `fragments` with a `state` and, if it was excluded, the
reasons. `installed` means it is in the realm.

**Deleting the ConfigMap removes everything it created — but not instantly.** A fragment
that goes missing is held for a grace period (five minutes by default) before its realm
objects are retracted, because a chart upgrade that deletes and recreates your ConfigMap
would otherwise take your client down and kill every session on it. In the meantime your
client stays exactly as it was and the status document reports your fragment as
`retracting`. If it comes back inside the window, nothing happened.

---

**Still to write:** the `SERVICE_ACCOUNT` and `TOKEN_EXCHANGE` shapes with examples; how
an app reads and enforces its roles; how this interacts with adding a
[launchpad chip](launchpad-chips.md); an ADR link once the design settles.
