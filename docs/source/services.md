# Services

The Scout Rad Report Explorer consists of several services that work together to process HL7 reports into a data lake, 
and provide a user interface for users to access and analyze the data. Below is a list of the main services that make up
the Rad Report Explorer.

## User Services

### JupyterHub

[JupyterHub](https://jupyterhub.readthedocs.io/en/stable/) is the primary interface for users to access rad report data.
It provides single-user [Jupyter notebooks](https://jupyter.org/), which can be used to run Python code and interact 
with report data in the data warehouse. On first login, users are given a Jupyter notebook with example code to extract 
and analyze data from the HL7 reports using [PySpark](https://spark.apache.org/docs/latest/api/python/index.html).

## Admin Services

The following admin services support the system's backend operations.

### Grafana

[Grafana](https://grafana.com/) is a monitoring and visualization tool used to track system performance and metrics.
Grafana dashboards for Temporal, MinIO, Kubernetes, and other services are available to system administrators.

### MinIO

[MinIO](https://min.io/) is an object storage service that is used to store HL7 reports and other data. Jupyter 
notebooks users can access MinIO to read the HL7 reports.

### Temporal

[Temporal](https://temporal.io/) is a workflow orchestration service that Scout uses to manage the ingestion and 
processing of HL7 reports.

