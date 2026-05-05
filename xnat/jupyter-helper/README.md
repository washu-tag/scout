# scout_xnat_auth — Jupyter helper for XNAT access (POC)

This is the Phase C POC helper module from the XNAT auth implementation
plan (`docs/internal/xnat-auth-implementation-plan.md`). It wraps
[xnatpy](https://xnat.readthedocs.io/) so notebooks running inside Scout
JupyterHub can call the in-cluster XNAT REST API authenticated as the
notebook owner — no copy-pasting tokens, no XNAT alias tokens, no Basic
auth.

## What it does

1. Reads the user's Keycloak access token out of JupyterHub's
   `auth_state` via the Hub REST API
   (`GET {JUPYTERHUB_API_URL}/users/{JUPYTERHUB_USER}` with
   `JUPYTERHUB_API_TOKEN`). The Scout deploy grants the spawned
   server's role `admin:auth_state!user` so this works.
2. Mints a Scout-issued `JSESSIONID` by hitting XNAT once with that
   bearer token. The `xnat-scout-auth-plugin`'s `BearerTokenFilter`
   does the JWT validation + token exchange + user provisioning on
   that first call.
3. Hands the `JSESSIONID` to `xnat.connect(jsession=...)`. The user
   gets the full xnatpy ORM and a keepalive thread that pings XNAT
   every ~7 min so the session stays alive.
4. Catches 401s from any subsequent xnatpy call, re-fetches the access
   token (fresh from Keycloak via the Hub's `refresh_user`), re-mints
   the `JSESSIONID`, and retries the original request once.

## Usage

```python
import scout_xnat_auth

connection = scout_xnat_auth.connect()
for project in connection.projects.values():
    print(project.id, project.name)
```

The default server URL is the in-cluster service
(`http://xnat.xnat.svc.cluster.local`) — DON'T point this at the
public ingress hostname; the public path goes through oauth2-proxy
and won't accept the bearer token.

## Installing into a notebook (POC, manual)

```bash
# From the repo root, with KUBECONFIG pointing at dev03:
NS=$(kubectl get pod -A -l app=jupyterhub --field-selector=status.phase=Running -o jsonpath='{.items[?(@.metadata.labels.component=="singleuser-server")].metadata.namespace}' | tr ' ' '\n' | head -1)
POD=$(kubectl -n $NS get pod -l component=singleuser-server -o jsonpath='{.items[0].metadata.name}')
kubectl -n $NS cp xnat/jupyter-helper/scout_xnat_auth.py $POD:/home/jovyan/scout_xnat_auth.py
kubectl -n $NS cp xnat/jupyter-helper/sample_xnat_notebook.ipynb $POD:/home/jovyan/sample_xnat_notebook.ipynb
```

(`xnatpy` is already in the JupyterHub singleuser image; no `pip install`
needed.)

## Why this isn't packaged

Per implementation plan §1.5, the POC version stays a single file —
the API surface is exactly what we want to learn from before deciding
whether to ship it as a Nexus-pip-proxy package, bake it into the
singleuser image, or keep handing it out as a sample. Re-evaluate
after the POC.
