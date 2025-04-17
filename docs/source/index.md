# Scout Radiology Report Explorer

Welcome to the Scout Radiology Report Explorer documentation! 

The Scout Radiology Report Explorer is designed to help users access and analyze large volumes of HL7 radiology reports.
HL7 radiology reports are processed and ingested into a data lake where they are made available for analysis. Jupyter
notebooks are provided to users to facilitate data exploration and analysis with Python and PySpark. Scout is backed by
Temporal for data ingestion and processing, Delta Lake and MinIO for efficient data storage and management, and Grafana
for monitoring and visualization.

## Quickstart

From the Scout landing page, launch JupyterHub to access a Jupyter notebook. The JupyterHub service is the primary
interface for users to access radiology report data. On first login, an example Jupyter notebook with sample code to 
access and analyze the radiology report data is provided. Run the `Scout/Quickstart.ipynb` notebook to get started with 
the data exploration process. The notebook provides example code to search for reports, filter by various criteria, and 
export the results to CSV files.

![Scout Quickstart Notebook](images/ScoutQuickstartNotebook.png)

After running the quickstart notebook, review the [Data Schema](dataschema.md) to understand the structure of the report
data in the data lake and the mapping of HL7 fields to the report table columns.

The [Services](services.md) page provides an overview of the main services 
that make up the Scout Rad Report Explorer.

```{toctree}
:hidden:
services
dataschema
```
