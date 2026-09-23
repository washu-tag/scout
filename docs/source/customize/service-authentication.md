# Authenticate Users to Your Service

Your Pluggable App likely needs to authenticate Scout users and know what permissions they have been assigned so you can decide what they're authorized to do in your app. This document will explain how to integrate with the Scout systems designed to enable you to do that.

This page assumes you've read [How Authentication Works](../reference/authentication.md) first.

## Summary: What your Pluggable App needs to include

| | You write | Purpose |
| --- | --- | --- |
| 1 | Ingress annotation | Puts your service behind the platform login gate |
| 2 | Fragment ConfigMap | Asks Scout to create your Keycloak client and roles |
| 3 | Secret | The client credential, which you generate |
| 4 | Role + RoleBinding | Lets Scout read that one Secret |
| 5 | Your app's OIDC config | Establishes who the user is inside your service |

## 1. Ingress

If your app needs to receive any user traffic or requests, you must define an Ingress. In general the Ingress defines the subdomain your app is reachable on, and how to connect requests to whatever ports your service is listening on. Example: https://github.com/washu-tag/scout/tree/main/examples/pluggable-app/templates/ingress.yaml

The important part for authentication is the Middleware annotations. You should include these in your chart's values file under `ingress.annotations`. This is what enables Scout's OAuth2 Proxy service to redirect all requests bound for your app to the Keycloak login system.

```yaml
ingress:
  annotations:
    traefik.ingress.kubernetes.io/router.middlewares: >-
      kube-system-oauth2-proxy-error@kubernetescrd,
      kube-system-oauth2-proxy-auth@kubernetescrd,
      kube-system-security-headers@kubernetescrd
```

Note that the `kube-system` part of the name is the namespace in which OAuth2 Proxy is deployed. If your site has customized that namespace, the Middleware name will be different, and you should substitute your site's OAuth2 PRoxy namespace for `kube-system`.

:::{note}
If your app requires any endpoints to be unauthenticated (e.g. favicons, any public pages), you will need to define a second Ingress for those specific paths without these middlewares. See, for example, Launchpad's [favicon-ingress.yaml](https://github.com/washu-tag/scout/tree/main/helm/launchpad/templates/favicon-ingress.yaml).
:::

## 2. A Fragment ConfigMap: Register a Keycloak client

In order for your app to be able to integrate with Scout's login system, you will need to define a Keycloak client for your app. Keycloak is how Scout manages user logins and service-level auth; see [Authentication](../reference/authentication.md) for a more in-depth reference.

The Scout Reconciler service will create the Keycloak client on your behalf. All you need to do is publish a ConfigMap which has the label `keycloak.scout.xnat.org/fragment: 'true'`, and which contains in its `data` a `KeycloakFragment` shaped like this:

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
          scout-admin: [my-service-user, my-service-admin]
```

See also the example pluggable app: https://github.com/washu-tag/scout/tree/main/examples/pluggable-app/templates/keycloak-fragment.yaml.

| Field | Meaning |
| --- | --- |
| `clientId` | The Keycloak client id that you want to assign to your client. Must be unique, or your Fragment will be rejected by the Reconciler. |
| `displayName` | Shown in the Keycloak admin console, not accessible to regular users. |
| `description` | Optional, free text. Shown in the Keycloak admin console, not accessible to regular users. |
| `appUrl` | Your service's base URL, i.e. how users will get to your app. It is usually `https://<your subdomain>.<scout base domain>` and should match your `Ingress`. |
| `redirectUris` | Where Keycloak may send a user back after login. **NOTE** the spelling is `redirectUris` with an `i`, not `redirectUrls` with an `l`. A typo here, or anywhere, will cause your fragment to be rejected. |
| `roles` | Your app's internal language for how it authorizes users. You can put whatever you want for the `roles` so long as your app knows what that means. Those values are what will be written into the tokens your app will receive from Keycloak. |
| `roleClaim` | Optional, defaults to `groups`. This controls in what token claim Keycloak will write the user's roles, and where in the token your app can expect to read them. |
| `secretRef` | `name`, and optionally `key`, of the Secret holding your credential. |
| `grants` | How you map Scout's user groups (`scout-user` and `scout-admin`) to your app's role vocabulary. The roles must exist in the `roles` section above. |

A bit more on `roles` and `grants`, since this is an important piece to understand if you are going to get auth right. When a Scout user gets authorized by an admin to use the platform, they will be added to `scout-user` to give them basic access, or `scout-admin` to give them admin access. As of writing those are the _only_ groups and only levels of permission Scout recognizes. In the `grants` section of the Fragment is where you can express something like "When a user is in `scout-user`, they should get these roles in my app: `[...]`". Those roles are the only thing you will see in the tokens that your service gets on user requests; you won't see `scout-user` or `scout-admin`, you'll see the roles that those two groups map to.

### The client secret

The `secretRef` in the Fragment needs to point to a Secret that exists on the cluster. You can create it within your app's chart, though it can be better to manage Secrets outside of helm charts. However it comes to be, you reference it by name in your Fragment. 

Its value will be used as your app's Keycloak client's secret. Both Keycloak and your app's service will need to know this secret value so your app is able to exchange a login code for tokens.

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

The key can be whatever you want, but if you use any key other than "`client-secret`" you need to set that key as `secretRef.key` in your Fragment.

### The RBAC grant for the Secret

This RBAC Role and RoleBinding are easy to miss, but very important. These are what allow the Reconciler service to read your app's client Secret, which it needs to do to create the Keycloak client from your Fragment. If you create the Secret and the Fragment but forget the Role and RoleBinding, the Reconciler won't be able to read the Secret and can't create your client. So don't forget these!

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

**Important notes**:

- The RoleBinding's `subjects` section is _not_ adjustable. The `ServiceAccount` name should be `scout-keycloak-fragment-reconciler` and the `namespace` should be whatever namespace your site installed Keycloak into, which is `scout-core` by default.
- The role can be named whatever you want, so long as it does not conflict with an existing role, and you match up the Role's `metadata.name` with the RoleBinding's `roleRef.name`.

## 3. Configure your app's OIDC client

This part is somewhat beyond the scope of this document. You'll need to know how your app manages and configures OIDC. This is something of a loose guide to how you should integrate what we've already discussed:

| Value | Where it comes from |
| --- | --- |
| Client ID | `clientId` in your fragment |
| Client secret | This is the secret value that you created and wrote into a Secret in [the client secret section](#the-client-secret). |
| Issuer | `https://keycloak.<scout-host>/realms/scout` |
| Redirect URI | your library's callback path |

The best practice with the client secret is to configure your app's Service to mount the Secret into the Pod and have your app read it from the environment, not to pass it through the helm values.

The redirect URI is the easiest value to get wrong. It can be different for every service, usually depending on the underlying OIDC library used. For example, here are some of the redirect paths used in Scout's core services.

| Application | Callback path |
| --- | --- |
| next-auth (launchpad) | `https://<scout host>/api/auth/callback/keycloak` |
| Flask-AppBuilder (Superset) | `https://<scout host>/oauth-authorized/keycloak` |
| Grafana | `https://<scout host>/login/generic_oauth` |
| JupyterHub | `https://<scout host>/hub/oauth_callback` |
| Open WebUI | `https://<scout host>/oauth/oidc/callback` |
| MinIO | `https://<scout host>/oauth_callback` |
| Temporal | `https://<scout host>/auth/sso/callback` |
| XNAT | `https://<scout host>/openid-login` |

You'll need to find yours in your library's docs. When you include it in the Fragment's `redirectUris` be sure to write it as the full URL with `https` (as in `https://<scout host>/<you app's path>`), or it will be rejected.

For a complete example, see [`launchpad/src/lib/auth.ts`](https://github.com/washu-tag/scout/blob/main/launchpad/src/lib/auth.ts), which is how Launchpad configures its `next-auth` library.

### What is _not_ adjustable in a Fragment

The Fragment syntax is deliberately a subset of what Keycloak's clients can configure. We do make some choices about aspects of your clients which may have consequences for their design. If any of these assumptions do not fit your app, please [submit a feature request](https://github.com/washu-tag/scout/issues/new?template=feature.md) describing your use case.

| Scout's decision | What your app must do |
| --- | --- |
| Confidential client | Keep the secret server-side; a pure browser app will not work |
| Authorization code flow only | No implicit flow |
| PKCE `S256` required | Send a code challenge — most libraries do by default ([RFC 7636](https://datatracker.ietf.org/doc/html/rfc7636)) |
| No direct access grants | You cannot get a token with a username and password; see [testing](#5-test-and-troubleshoot) |
| No service accounts | No `client_credentials` grant for machine-to-machine calls |
| Sees only its own roles | Your token names your roles and no other service's |

## 4. Read the token and act on roles

After login, Keycloak redirects the user back to your redirect URI with an authorization code. Your app's backend then exchanges that code, along with the app's client secret, at Keycloak's token endpoint. Keycloak responds with an **ID Token**, an **Access Token**, and a **Refresh Token**. Your app should validate the ID token (and maybe the access token) and read the user's roles from it to make authorization decisions.

It will typically be easier if you let some library validate the tokens for you and handle the login. If you decide to validate them yourself, you'll need to do some things the same way for both access and ID tokens:

1. Read `kid` from the token header.
2. Fetch the matching key from the JWKS endpoint, and cache it.
3. Check the algorithm against your own allowlist — never trust the token's `alg`.
4. Verify the signature.
5. Verify `iss` and `exp`.

There are additional steps you can do which vary between access and ID tokens.

### ID tokens

Every app receives an ID token at login. In addition to the steps above:

- Verify `aud` contains your client ID. If `aud` names more than one client, also verify `azp` is your client ID.
- Verify `nonce` matches the one you sent, if you sent one.

### Access tokens

You only need to validate access tokens if your app accepts them in an `Authorization: Bearer` header, for instance from your own frontend calling your API. In addition to the steps above:

- Verify `typ` is `Bearer`, so that an ID token presented as a bearer token is rejected.
- Verify `azp` is your client ID. Do not require `aud` to be your client ID; Keycloak only includes your client ID in an access token's `aud` when the user holds one of your roles.

Alternatively, you can send the access token to Keycloak's introspection endpoint rather than validating it locally. That costs a request per check, but catches sessions that have been revoked before the token expires.

This advice should, of course, be treated with a huge degree of skepticism. Your app's security relies on proper handling of these tokens, so you should definitely not take this doc's word as final and authoritative on how to do that. Scout can provide you the tokens, but what you do with them is ultimately your responsibility.

See Keycloak's docs on [OIDC endpoints](https://www.keycloak.org/securing-apps/oidc-layers) for another perspective.

### Users and administrators

Once you have validated the token, the user is Authenticated and you can make Authorization decisions. Roles will arrive under whatever claim you specified as `roleClaim` in the Fragment; if you left this with the default value the roles are in the `groups` claim. The claim is written into the ID token, the access token, and the userinfo response, so read it from whichever your library gives you.

The role values belong to your app. They are whatever you defined in the `roles` section of the Fragment. Your app must define what they mean. Note that it is possible for the token to contain no claim or an empty claim, which means the user has no roles assigned for your app.

## 5. Test and troubleshoot

### Was the fragment applied?

When you publish a Fragment in a ConfigMap, the Reconciler service will pull it in and parse it. It will publish a status outcome as an Event, which you can see by `describe`-ing that ConfigMap.

```console
$ kubectl describe configmap -n my-service my-service-keycloak
...
Events:
  Type     Reason            Message
  Warning  FragmentInvalid   my-service: redirectUris 'https://elsewhere.example.com/cb'
                             points at elsewhere.example.com, which is outside the site's
                             own domain (scout.example.edu)
```

| Reason | Meaning |
| --- | --- |
| `FragmentApplied` | A fragment has been newly applied. |
| `FragmentInvalid` | The document is wrong. Fix and reapply. |
| `FragmentRejected` | Something else already owns that `clientId`. |
| `FragmentFailed` | Keycloak refused, or the `secretRef` could not be read — usually the missing grant. |

The error statuses will be re-published every time the Reconciler resyncs and checks all its Fragments for changes\*, so if you see one of those error states it should persist long enough for you to notice and resolve the problem. The `FragmentApplied` state is only published once when the Fragment is first picked up and applied (and after any change), so it will last only as long as Kubernetes persists the Event (one hour by default). So if you see no Events on your ConfigMap it means one of two things:
1. Your Fragment was correctly applied, but it has been long enough that the Event was cleaned up. In that case you should be able to see your app's client in the Keycloak Admin UI.
2. Your Fragment was not picked up by the Reconciler at all. That probably means the Reconciler could not find your ConfigMap in the first place. Check you've labelled it with exactly `keycloak.scout.xnat.org/fragment: "true"`. Without that label your ConfigMap is invisible to the Reconciler and nothing ever knows that it exists.

\* The Reconciler by default is set up to resync every 300 seconds. But if it is set up with `resync_seconds: -1`, it never periodically resyncs. That means the error states will also only show up once and be cleaned up after one hour.

### Symptoms

| What you see | Usual cause |
| --- | --- |
| Client never appears in Keycloak | Missing or misnamed RBAC grant; wrong namespace in the RoleBinding |
| `invalid_redirect_uri` at login | `redirectUris` does not exactly match your library's callback path |
| Redirect loop, or your app never sees a login | Ingress middleware annotation missing or misordered |
| `groups` claim is empty | User is in no tier you grant to, or not yet approved |
| Administrators denied, ordinary users fine | Admin tier not granted your `-user` role |
| `aud` mismatch validating a token | Requiring `aud` on an access token; check `azp` instead, or validate the ID token |
| PKCE error from Keycloak | Your library is not sending an `S256` code challenge |

### Removal

If you remove your app, and more specifically delete the Fragment ConfigMap, the Reconciler will detect this and remove your app's Keycloak client after a few minutes' grace period. The purpose of the grace period is so a `helm uninstall` followed by a reinstall shortly after does not cause the Keycloak client (and thus your app) to be unavailable during that period.
