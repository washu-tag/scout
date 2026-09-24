# Monitoring

Scout includes services for monitoring. These can collect logs and metrics from your service, and display them in a Grafana dashboard. This document describes what you need to do to start monitoring your Pluggable App.

## Logs

Every pod's stdout and stderr are collected automatically. Any logs collected in your application should be emitted to stdout, and they will show up in Grafana under **Explore > Loki**.

## Metrics

Prometheus scrapes metrics from any pod that carries these annotations:

```yaml
spec:
  template:
    metadata:
      annotations:
        prometheus.io/scrape: 'true'
        prometheus.io/port: '8080'
        prometheus.io/path: /metrics
```

Make sure to put the annotations on the pod template, not on the Service. 

If you're completely new to metrics, consult the Prometheus [Getting Started](https://prometheus.io/docs/tutorials/getting_started/) guide.

### NetworkPolicy

This is an optional security hardening component. If you already have a NetworkPolicy which restricts where traffic can reach your service from, you will need to open a path for Prometheus to reach you to scrape metrics. Example:

```yaml
ingress:
  - from:
      - namespaceSelector:
          matchLabels:
            kubernetes.io/metadata.name: scout-monitoring
        podSelector:
          matchLabels:
            app.kubernetes.io/name: prometheus
            app.kubernetes.io/component: server
    ports:
      - protocol: TCP
        port: 8080
```

Note that `scout-monitoring` is the default monitoring namespace, but it may be different on your site.

## Dashboards

Once your service is emitting metrics which are being collected, you'll want a way to visualize them. If you've never made a Grafana dashbaord before, see their [Create Dashboards](https://grafana.com/docs/grafana/latest/visualizations/dashboards/build-dashboards/create-dashboard/) guide. 

You can create a dashboard using Scout's Grafana UI, but it will not be saved there. Scout's Grafana only persistently displays dashboards which are written in labelled ConfigMaps in the Scout cluster. After creating the dashboard, export the JSON, and include it in a ConfigMap labelled `grafana_dashboard: "1"` in your Pluggable App's helm chart. Example:

```yaml
apiVersion: v1
kind: ConfigMap
metadata:
  name: my-service-dashboard
  namespace: my-service
  labels:
    grafana_dashboard: '1'
data:
  my-service.json: |
    { "uid": "my-service", "title": "My Service", ... }
```

That will cause Grafana to pick up the ConfigMap and import your dashboard, to be displayed alongside Scout's first-party dashboards.

:::{note}
There can be collisions between your dashboards and Scout's with the name of the data key in your ConfigMap and the dashboard's UID. As in, if you choose a `my-service.json` key in the ConfigMap, or a `"uid": "my-service"` in the JSON, and either of those is the same as some other dashboard in Scout, yours or theirs will be overwritten. To avoid those collisions, we recommend that your ConfigMap data key and your dashboard uid(s) all be prefixed with your service's name.
:::

:::{note}
Your dashboards will need to query data from a specific datasource. We recommend that you not use datasource UIDs for this, as they might change from site to site; if you use a specific site's datasource UIDs your Pluggable App would not be portable. We recommend you use `datasource`-type template variables for Prometheus and Loki (our metrics and logs datasources, respectively), and reference them throughout your dashboard as `${datasource}`. You can see an example of this in [examples/on-prem-pluggable-app/files/dashboard.json](https://github.com/washu-tag/scout/tree/main/examples/on-prem-pluggable-app/files/dashboard.json).
:::

## Not supported

Currently we do not support adding Grafana alerts or new datasources from Pluggable Apps. If you need one of these, please [submit a feature request](https://github.com/washu-tag/scout/issues/new?template=feature.md) describing your use case.
