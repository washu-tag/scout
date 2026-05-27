# scout-xnatpy

> **Status: checked in for review.** This library is the Scout-side
> half of the XNAT-auth POC. Its companion is the
> [`scout-xnat-auth-plugin`](../scout-xnat-auth-plugin/), an XNAT
> server plugin that validates the bearer token this library forwards.
> Neither half has a consumer in `main` yet — Scout doesn't deploy
> XNAT, and the POC consumers (a sample Jupyter notebook and a FastAPI
> demo service) live on the original POC branch (`xnat-dev-wt`). The
> code is in-tree so reviewers can read it and so the work isn't lost.
>
> Distribution name is `scout-xnatpy` (matches the directory); the
> importable module is `scout_xnat`. The two names diverge so the
> import path can stay stable if the package later grows beyond auth.

Bearer-token XNAT auth wrapper used by Scout Python callers.

The package wraps [xnatpy](https://xnat.readthedocs.io/) and handles:

1. Fetching the user's Keycloak access token from a configurable token
   provider.
2. Minting an XNAT `JSESSIONID` by hitting XNAT once with that token —
   `scout-xnat-auth-plugin`'s `BearerTokenFilter` validates, runs
   token exchange, provisions the user, and sets the cookie.
3. Handing the `JSESSIONID` to `xnat.connect(jsession=...)` so the
   caller gets the full xnatpy ORM and the keepalive thread.
4. Catching 401s from xnatpy calls, re-fetching the access token via
   the provider, re-minting the `JSESSIONID`, and retrying once.

## Two consumers, one package

* **JupyterHub singleuser pods.** `scout_xnat.connect()` with no
  `token_provider` defaults to the Hub-API provider, which reads the
  user's `auth_state.access_token` via `GET {JUPYTERHUB_API_URL}/users/
  {JUPYTERHUB_USER}` (using `JUPYTERHUB_API_TOKEN` from the env).
* **`scout-xnat-frontend` FastAPI service** (POC, branch-only). The
  service receives the access token in the `X-Auth-Request-Access-Token`
  header (forwarded by oauth2-proxy) and passes a per-request
  `token_provider` lambda reading from the request scope.

## Usage

```python
import scout_xnat

# Jupyter default (Hub-API provider)
conn = scout_xnat.connect()
for p in conn.projects.values():
    print(p.id, p.name)

# Service path: token from a header
conn = scout_xnat.connect(
    server="http://xnat.xnat.svc.cluster.local",
    token_provider=lambda: request.headers["X-Auth-Request-Access-Token"],
)
```

The default server URL is the in-cluster service
(`http://xnat.xnat.svc.cluster.local`) — DON'T point at the public
ingress hostname, since the public path goes through oauth2-proxy and
won't accept the bearer token.

## Layout

```
scout_xnat/
├── __init__.py        # public API: connect(), TokenProvider, errors
├── client.py          # connect() + xnatpy wrapping + JSESSIONID minting
├── token_providers.py # JupyterHubTokenProvider; TokenProvider type alias
├── retry.py           # 401-retry response hook
└── errors.py          # ScoutXnatAuthError + XnatBearerRejected / XnatUnreachable
```

## Installing into a Jupyter notebook (POC, manual)

The whole `scout_xnat/` package directory is copied into the
notebook's home dir and imported by name. Python's import system
finds `scout_xnat/` under `/home/jovyan/` and treats it as a package.

```bash
# From the repo root, with KUBECONFIG pointing at the cluster:
NS=$(kubectl get pod -A -l app=jupyterhub --field-selector=status.phase=Running -o jsonpath='{.items[?(@.metadata.labels.component=="singleuser-server")].metadata.namespace}' | tr ' ' '\n' | head -1)
POD=$(kubectl -n $NS get pod -l component=singleuser-server -o jsonpath='{.items[0].metadata.name}')
kubectl -n $NS cp scout-xnatpy/scout_xnat $POD:/home/jovyan/scout_xnat -c notebook
```
