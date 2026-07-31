# Scout

Welcome to the Scout documentation!

Scout is a data exploration and clinical insights platform designed to help users access and analyze large volumes of medical imaging data. Data are processed and ingested into a data lake where they are made available for exploration and analysis through multiple interfaces.

The first release of Scout is focused on HL7 radiology reports. Future versions will incorporate DICOM, pathology reports, and extracted features.

## Quickstart

### Scout Launchpad

After [authentication](authentication.md), your initial landing page is the Scout Launchpad. From here, you can access all Scout services, including Analytics, Notebooks, Chat (if enabled), and this documentation site.

![Scout Launchpad](images/ScoutLaunchpad.png)

### Core Services

Scout provides three primary interfaces for working with data:

#### Scout Analytics

Selecting **Analytics** from the Launchpad takes you to Apache Superset, a powerful data visualization and exploration tool. Your initial landing page is the Scout Dashboard, designed to give you an overview of all report data. From here, you can analyze and explore the data either [graphically](https://superset.apache.org/docs/using-superset/creating-your-first-dashboard) using the visualization builder or via [SQL Lab](https://superset.apache.org/docs/using-superset/using-sql-lab/) for direct SQL queries. See the [Trino SQL documentation](https://trino.io/docs/current/language.html) for SQL syntax reference.

![Scout Dashboard](images/ScoutDashboard.png)

#### Scout Chat

Selecting **Chat** from the Launchpad provides an AI-powered interface for natural language querying of radiology reports. Ask questions in plain English and receive data-driven answers powered by large language models with direct access to the Scout Delta Lake. Learn more in the [Chat documentation](chat.md).

**Note:** The Chat service is optional and may not be available in all Scout deployments.

![Scout Chat](images/ScoutQuery.png)

#### Scout Notebooks

Selecting **Notebooks** from the Launchpad launches JupyterHub with notebooks that query the data lake through [Trino](https://trino.io/) for fully customizable data analysis. An example Jupyter notebook with sample code to access and analyze data is provided in `Scout/Quickstart.ipynb`. This notebook provides example code using pandas with SQL to search for reports, filter by various criteria, and export results to CSV files.

![Scout Quickstart Notebook](images/ScoutQuickstartNotebook.png)

## Documentation

Scout's documentation is organized by audience:

* **[Using Scout](user/index.md)** — explore and analyze data: logging in, Analytics, Chat, Notebooks, and understanding what data you can see.
* **[Operating Scout](operate/index.md)** — deploy and run Scout: the Ansible inventory, air-gapped installation, report ingestion, and per-user data-authorization configuration.
* **[Reference](reference/index.md)** — the report data schema, backend architecture, and supporting database schemas.

* **Report Issues**: Submit issues on [GitHub](https://github.com/washu-tag/scout/issues)

```{toctree}
:hidden:
user/index
operate/index
reference/index
```
