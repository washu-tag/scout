# Give a Service Its Own Keycloak Client

A service that logs users in needs a Keycloak client. To get one, publish a ConfigMap — in
your service's own namespace — labelled `keycloak.scout.xnat.org/fragment: "true"`. Scout's
fragment reconciler picks it up, creates the client and its roles in the Scout realm, and
wires those roles into the Scout user tiers. Uninstalling your service removes the
ConfigMap, and the client goes with it.

No edit to Scout, no realm file, no redeploy of Keycloak. This works the same for Scout's
own components and for anything else running in the cluster: a working login follows
installation, the way a launchpad chip does (see [chips](launchpad-chips.md)).

## A minimal example

Three objects: the fragment, a Secret holding the client credential, and a grant letting
the reconciler read that Secret.

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
        description: One line about what it does
        appUrl: https://my-service.scout.example.edu
        redirectUris:
          - https://my-service.scout.example.edu/auth/callback
        roles:
          - my-service-user
          - my-service-admin
        secretRef:
          name: my-service-keycloak-client
        grants:
          scout-user: [my-service-user]
          scout-admin: [my-service-admin]
```

Your users then arrive with `my-service-user` (or `my-service-admin`) in the `groups` claim
of the token your app receives, and your app decides what that means. A Scout user in none
of the groups your fragment grants gets an empty `groups` claim.

`helm/hello-scout/` in the Scout repository is a complete working example.

## The credential, and the grant that makes it readable

A fragment never contains a secret, only a `secretRef` naming one. You create that Secret;
Scout reads it and installs the value as the client's credential. Nothing flows back: no
credential is ever generated or written into Kubernetes for you.

```yaml
apiVersion: v1
kind: Secret
metadata:
  name: my-service-keycloak-client
  namespace: my-service
type: Opaque
stringData:
  client-secret: <a long random string>
```

The key must be `client-secret` unless your fragment says otherwise with
`secretRef.key`.

**This next part is the one people miss.** Scout holds no standing permission to read
Secrets anywhere. Your chart has to grant access to yours, and only yours:

```yaml
apiVersion: rbac.authorization.k8s.io/v1
kind: Role
metadata:
  name: my-service-keycloak-secret-reader
  namespace: my-service
rules:
  - apiGroups: ['']
    resources: ['secrets']
    verbs: ['get']
    resourceNames:
      - my-service-keycloak-client
---
apiVersion: rbac.authorization.k8s.io/v1
kind: RoleBinding
metadata:
  name: my-service-keycloak-secret-reader
  namespace: my-service
roleRef:
  apiGroup: rbac.authorization.k8s.io
  kind: Role
  name: my-service-keycloak-secret-reader
subjects:
  - kind: ServiceAccount
    name: scout-keycloak-fragment-reconciler
    namespace: scout-core
```

The ServiceAccount name above is a stable contract; the `resourceNames` list is what keeps
the grant to your one Secret. Omit this and your fragment is rejected for an unreadable
`secretRef` — the symptom to look for if a client never appears.

The namespace is wherever the reconciler runs, which is Scout's Keycloak namespace —
`scout-core` by default, and whatever your site set if it overrides namespaces. A
RoleBinding naming a namespace the reconciler is not in grants nothing and presents
exactly like a missing one.

A credential rotation is picked up on the next periodic pass rather than immediately,
because `resourceNames` can scope a `get` but not a `watch`.

## What a fragment can say

| Field | Meaning |
| --- | --- |
| `clientId` | The Keycloak client id. Must not collide with an existing client. |
| `displayName` | Shown in the Keycloak admin console. |
| `description` | Optional. Free text. |
| `appUrl` | Your service's base URL. Becomes the post-logout redirect as written, and the web origin reduced to `scheme://host[:port]`. |
| `redirectUris` | Where Keycloak may send a user back after login. Name each one in full. |
| `roles` | Optional. The role vocabulary your app understands. |
| `roleClaim` | Optional. Which token claim carries those roles; defaults to `groups`. Set it only if your service reads roles from somewhere else. |
| `secretRef` | `name`, and optionally `key`, of the Secret holding your credential. |
| `grants` | Optional. Which of your roles each Scout tier confers. |

Five rules, each a rejection rather than a warning:

- **`redirectUris` and `appUrl` must be `https` and under your site's own domain.** No
  wildcards, no credentials in the URL. Unconstrained, a redirect URI is a way to have
  Keycloak hand a user's token to an arbitrary host.
- **`appUrl` must not carry a query string.** It is a base URL, and a query is meaningless
  in both things it becomes. A `redirectUris` entry may carry one.
- **`grants` may only name roles your own fragment declares.**
- **`grants` may only target Scout's tier roles** (`scout-user`, `scout-admin`).
- **`roleClaim` may not be a standard claim** like `sub`, `aud`, or `resource_access`.

`appUrl` may carry a path — an app served under `/my-service` is fine. Only the derived
web origin drops it, because a browser's `Origin` header is an origin and never carries
one.

## What a fragment cannot say, on purpose

The vocabulary is narrower than Keycloak's by design. Scout decides — identically for
every fragment — that your client is confidential, that it uses the browser authorization
code flow and no other, that PKCE `S256` is enforced, that it sees only its own roles and
no other app's, which protocol mapper publishes your `roleClaim`, and which client scopes
it gets. A fragment naming any of those is rejected, so a field that looks like it took
effect always did.

Your service's OIDC client must therefore send a PKCE code challenge; every maintained
OIDC library can, and most do by default.

### Client scopes are set once

The client scopes Scout assigns are applied when your client is created and are immutable
afterwards. Keycloak applies the scope lists on client creation and ignores them on
client update — there are
[separate endpoints](https://www.keycloak.org/docs-api/latest/rest-api/index.html#_clients)
for changing them, and Keycloak has
[declined](https://github.com/keycloak/keycloak/issues/24920) to make the update path
honour them. Scout therefore does not reconcile them: it cannot repair a change made
elsewhere, and trying would rewrite your client on every pass without ever fixing
anything.

In practice this only matters if someone edits the scopes in the admin console. They will
stay edited, and Scout will not report it. Deleting the client — which means removing your
fragment, waiting for the client to be collected, then re-adding it — is what restores
them.

Unknown fields are an error, not a warning: a misspelled `redirectUrls` fails the whole
document rather than quietly creating a client with no redirect URIs.

If your service needs something the vocabulary has no syntax for, that is a deliberate
change to Scout rather than something to work around. Open an issue.

## Versioning

`apiVersion: keycloak.scout.xnat.org/v1alpha1` is the contract. A fragment declaring a
version Scout does not know is skipped whole, with a message. `v1alpha1` may change; when
it does, both will be understood for a transition period.

## When it does not work

A fragment is applied within seconds of being written, and again on every periodic pass.
The outcome is reported as Kubernetes Events against your ConfigMap, which is the first
place to look:

```console
$ kubectl describe configmap -n my-service my-service-keycloak
...
Events:
  Type     Reason            Message
  Warning  FragmentInvalid   my-service: redirectUris 'https://elsewhere.example.com/cb'
                             points at elsewhere.example.com, which is outside the site's
                             own domain (scout.example.edu)
```

The reasons are `FragmentApplied`, `FragmentInvalid` (the document is wrong — fix and
reapply), `FragmentRejected` (something else owns that `clientId`), and `FragmentFailed`
(Keycloak refused, or the `secretRef` could not be read — usually the missing grant above).
Events mark changes, so a fragment that is applied and stays applied reports once.

A broken fragment affects only itself. It does not block other fragments, and it does not
remove a client it previously created — so a typo costs an unapplied change, not a login
outage.

If nothing happens at all, check the label is exactly
`keycloak.scout.xnat.org/fragment: "true"`. An unlabelled ConfigMap is invisible, and
nothing logs that it exists.

## Removal

Delete the ConfigMap and the client is removed after a grace period of a few minutes. A
Helm upgrade that leaves the ConfigMap's name unchanged is an update and never passes
through deletion; a `helm uninstall` followed promptly by a reinstall is noticed as a
return rather than a removal.
