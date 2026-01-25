# HL7 Listener Monitoring (POC)

This directory contains Prometheus scrape configs and Grafana dashboards for monitoring the HL7 listener's Kafka, Strimzi, and Camel K components.

## Status: POC - Manual Integration Required

These monitoring assets are **not automatically deployed** with the HL7 listener. The metrics endpoints are enabled (Kafka exposes metrics on port 9404, Camel K integrations expose metrics on `/q/metrics`), but Prometheus and Grafana need manual configuration to collect and visualize them.

## Contents

| File | Description |
|------|-------------|
| `prometheus-scrape-configs.yaml` | Prometheus scrape jobs for Strimzi operator, Kafka brokers, and Camel K integrations |
| `kafka-strimzi-dashboard.json` | Grafana dashboard for Kafka cluster health and HL7 topic throughput |
| `camel-k-dashboard.json` | Grafana dashboard for Camel K integration metrics |

## Integration Instructions

### 1. Add Prometheus Scrape Configs

Copy the contents of `prometheus-scrape-configs.yaml` to:
```
ansible/roles/prometheus/templates/values.yaml.j2
```

Add the jobs under the `extraScrapeConfigs` section (after the existing jobs like `superset-statsd`).

### 2. Add Grafana Dashboards

Copy the dashboard files to:
```
ansible/roles/grafana/templates/dashboards/
```

Rename with `.j2` extension:
- `kafka-strimzi-dashboard.json` → `kafka-strimzi-dashboard.json.j2`
- `camel-k-dashboard.json` → `camel-k-dashboard.json.j2`

### 3. Deploy Changes

```bash
cd ansible
make install-monitoring
```

