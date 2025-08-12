# Models

Scout's ML model deployment and serving infrastructure; To be completed.

For now this contains directions to deploy a prototype infrastructure:
- Ollama to serve public models
- Open WebUI, a web interface to chat with LLMs on Ollama
- An MCP service to enable an LLM to query our data

## Deploy Open WebUI + Ollama
In this step, we will deploy Open WebUI using its helm chart, which also deploys Ollama using its helm chart.

Create a namespace
```
kubectl create ns ollama
```

Add the open-webui helm repo
```
helm repo add open-webui https://helm.openwebui.com/
```

Open WebUI wants to use a PostgreSQL database. We will create a new database for it to use in our PostgreSQL.
First run a pod that can access the database.
```
kubectl -n cloudnative-pg exec -it pod/postgresql-cluster-1 -- psql --dbname=ingest
```
Enter the password when the pod starts. Note we are using the superuser `postgres`.

Create the database and give the `scout` user permissions.
```sql
CREATE DATABASE openwebui;
GRANT ALL PRIVILEGES ON DATABASE openwebui TO scout;
```
Switch to the database.
```sql
\c openwebui
```
Now set the rest of the permissions.
```sql
-- Grant usage on the public schema (needed for table operations)
GRANT USAGE ON SCHEMA public TO scout;

-- Grant create privileges on the public schema (allows creating tables)
GRANT CREATE ON SCHEMA public TO scout;

-- Set default privileges for future tables and sequences created by any user
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT ALL ON TABLES TO scout;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT ALL ON SEQUENCES TO scout;
```

Remove Superset. This is because the default Scout deployment is configured for the root path to route to Superset.
But Open WebUI also only wants to be deployed to the root of the domain.
In order to use Open WebUI we must remove Superset.
```
helm -n superset uninstall superset
```

Lastly, you may need to customize the `models/open-webui/values.yaml` file.
- `ollama.ingress.hosts[0].host` is set to `tagdev-control-03.minmi-algol.ts.net`
- `ingress.host` is set to `tagdev-control-03.minmi-algol.ts.net`
- `extraResources[1].spec.hostPath.path` is the local path for Open WebUI data; I set it to `/scout/persistence/open-webui`
- `extraResources[4].spec.hostPath.path` is the local path for Ollama data; I set it to `/scout/persistence/ollama`
- `databaseUrl` is a connection string for Postgres, with username and password embedded. Customize it to your values.

Now we can deploy Open WebUI + Ollama + a Redis instance for managing websockets using the helm chart.
```
helm install open-webui open-webui/open-webui --namespace ollama --values models/open-webui/values.yaml
```

Open WebUI should be available at the root of your domain.

## Deploy Trino MCP + Open WebUI MCPO
This sets up an MCP server that can help an LLM query our report data in Delta Lake via our Trino service.
In addition, because Open WebUI doesn't speak MCP directly, we have to deploy a proxy service to convert the MCP into OpenAPI for Open WebUI to use.

```
ansible-playbook -v -i inventory.yaml  --diff --vault-password-file vault/pwd.sh -l tagdev-control-03.nrg.wustl.edu playbooks/mcpo.yaml
```

Now we can go into Open WebUI's admin settings > Tools and add the proxy service as a tool, at URL `https://tagdev-control-03.nrg.wustl.edu/mcpo-trino`.

Reload the page and there should be a new `Trino MCP Server` tool available from the "wrench icon" menu on the chat.

## Deploy Ollama Standalone (If you don't want Open WebUI)
This step is to deploy Ollama on its own. It is not necessary if you also deploy Open WebUI, because it is configured to deploy Ollama as well.

Create a namespace for ollama
```
kubectl create ns ollama
```

We must create a volume to store the model data on a spacious part of our disk. For me, that is `/scout/persistence/ollama`.
Use the file `models/ollama/pv.yaml` in this repo; change the path on the last line to your desired path.
Then create it with
```
kubectl apply -f models/ollama/pv.yaml
```

We will serve Ollama at a subpath, but it wasn't designed to do that.
We need to create a traefik `Middleware` to strip the subpath.
```
kubectl apply models/ollama/middleware.yaml
```

Now we can deploy Ollama with helm.
You'll need to edit the values file to change the `ingress.hosts[0].host` to your host instead of `big-03.minmi-algol.ts.net`.
```
helm install ollama otwld/ollama --namespace ollama --values models/ollama/values.yaml
```
