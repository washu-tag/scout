# Services

The Scout Radiology Report Explorer consists of several services that work together to process HL7 radiology reports into a 
data lake, and provide a user interface for users to access and analyze the data.

![Scout System Overview](images/ScoutSystemOverview.png)

Below is a list of the main services that make up Scout.

## User Services

### Analytics

[Apache Superset](https://superset.apache.org/) allows users to explore and visualize the radiology report data with a simple, no-code 
visualization builder or using an integrated SQL IDE.

Under the hood, Superset is able to query the Lake using the [Trino](https://trino.io/) database engine.

### Notebooks

[JupyterHub](https://jupyterhub.readthedocs.io/en/stable/) an interface for power-users to access rad report data.
It provides single-user [Jupyter notebooks](https://jupyter.org/), which can be used to run Python code and interact 
with report data in the Lake. On first login, users are given a Jupyter notebook with example code to extract 
and analyze data from the HL7 reports using [PySpark](https://spark.apache.org/docs/latest/api/python/index.html).

The [Jupyter AI](https://jupyter-ai.readthedocs.io/en/latest/) extension is also available to users, which provides
AI-powered code suggestions and completions to help users write queries and analyze data. Configure the Jupyter AI 
extension with your LLM provider of choice (e.g., OpenAI, Anthropic, etc.) to enable this feature.

**Important:** Notebook servers automatically shut down after 2 days to conserve resources. Your files in `/home/jovyan/`
are preserved, but in-memory variables (DataFrames, models, etc.) are lost. Save your work regularly and checkpoint
intermediate results. See [Tips & Tricks](tips.md#notebooks) for checkpointing strategies.

## Application Services

The following services support the system's backend operations.

### Monitor

[Grafana](https://grafana.com/) is a monitoring and visualization tool used to track system performance, metrics, and 
logs. Scout provides pre-configured Grafana dashboards for services such as Temporal, MinIO, Kubernetes, and others. 
These dashboards are available to system administrators for monitoring and troubleshooting purposes. Additionally, logs
for each service can be accessed directly through Grafana.

(orchestrator_ref)=
### Orchestrator

[Temporal](https://temporal.io/) is a workflow orchestration service that Scout uses to manage the execution of its Extractor services. 
Workflow information is available in the Temporal UI and can be used to monitor and debug the data ingestion process.

(extractor_ref)=
### Extractor

Extractor services are responsible for extracting data from hospital information systems, and landing it into the Lake service.
Currently, Scout contains an HL7 Extractor that parses HL7 files from log files that contain a daily export of all HL7 radiology reports.
These logs are produced from a custom WashU HL7 subscriber service (external to TAG).

For visibility into the Extractor services, administrators should visit the HL7 Ingest Dashboard within Monitor 
(log information in Drilldown > Logs may be useful, as well). The Orchestrator UI provides a live look at running Extractors.

### Lake

The Lake service houses the Scout Data Lake in a medallion architecture. At the bronze level, raw HL7 files are available. At the silver level, these
files are transformed into the Scout [Data Schema](dataschema.md). 

Under the hood, the Lake service leverages a [Delta Lake](https://delta.io/) Lakehouse, backed by [MinIO](https://min.io/) distributed object storage. 
A [Hive Metastore](https://hive.apache.org/docs/latest/adminmanual-metastore-administration_27362076/) manages catalog metadata for the Lake.

Within Notebooks, users can access MinIO and Hive services to read the HL7 radiology report data.
