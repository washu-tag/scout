# Scout - Radiology Report Explorer

A distributed data analytics platform for intelligent, intuitive exploration of HL7 radiology reports. Scout processes large volumes of HL7 messages into a Delta Lake, making them accessible through interactive analytics and notebooks.

## Quick Links

- **Documentation**: https://washu-scout.readthedocs.io/en/latest/
- **Issue Tracker**: https://xnat.atlassian.net/jira/software/projects/SCOUT/summary
- **AI Assistant Guide**: See [CLAUDE.md](CLAUDE.md) for comprehensive codebase documentation

## Key Features

- **Analytics**: Apache Superset for no-code visualizations and SQL queries
- **Notebooks**: JupyterHub with PySpark for programmatic data analysis
- **Data Lake**: Delta Lake on MinIO with Hive Metastore catalog
- **Query Engine**: Trino for distributed SQL queries
- **Orchestration**: Temporal workflows for HL7 ingestion pipeline
- **Monitoring**: Grafana dashboards for observability
- **Deployment**: Automated Ansible deployment on Kubernetes (K3s)

## Architecture

```
HL7 Logs → Temporal → Extractor → Bronze (MinIO) → Transformer → Silver (Delta Lake)
                                                                        ↓
                                                              Trino ← Superset/JupyterHub
```

## Getting Started

### Deploy Scout

From the `ansible/` directory:

```bash
# 1. Configure your environment
cp inventory.example.yaml inventory.yaml
# Edit inventory.yaml with your hosts, paths, and secrets

# 2. Deploy full platform
make all

# Or deploy individual components
make install-k3s          # Kubernetes cluster
make install-lake         # MinIO + Hive
make install-analytics    # Trino + Superset
make install-orchestrator # Temporal workflow engine
make install-jupyter      # JupyterHub notebooks
make install-monitor      # Grafana monitoring
```

See [ansible/README.md](ansible/README.md) for detailed deployment documentation.

### Ingest HL7 Reports

```bash
kubectl exec -n temporal -i deployment/temporal-admintools -- temporal workflow start \
  --task-queue ingest-hl7-log \
  --type IngestHl7LogWorkflow \
  --input '{"logsRootPath": "/data/hl7", "reportTableName": "reports"}'
```

### Query Data

**In Superset SQL Lab:**
```sql
SELECT * FROM delta.default.reports WHERE modality = 'CT' LIMIT 100;
```

**In JupyterHub:**
```python
df = spark.read.table("delta.default.reports")
df.filter(df.modality == "MRI").show()
```

## Project Structure

- **ansible/**: Deployment automation (Ansible playbooks and roles)
- **docs/**: User and developer documentation
- **explorer/**: Web-based landing page (React/TypeScript)
- **extractor/**: HL7 processing services (Python/TypeScript)
- **orchestrator/**: Temporal workflows (TypeScript)
- **helm/**: Helm chart configurations
- **tests/**: Integration and unit tests

## Documentation

For comprehensive information, visit our [documentation](https://washu-scout.readthedocs.io/en/latest/):

- **Quickstart**: Getting started with Scout Analytics and Notebooks
- **Data Schema**: HL7 field mappings and table structure
- **Services**: Architecture and component overview
- **Ingestion**: HL7 report processing workflows
- **Tips & Tricks**: Usage tips for Grafana, Superset, and JupyterHub

## License

See LICENSE file for license information.
