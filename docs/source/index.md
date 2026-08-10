# Scout

Welcome to the Scout documentation!

Scout is a data exploration and clinical insights platform designed to help users access and analyze large volumes of medical imaging data. Data are processed and ingested into a data lake where they are made available for exploration and analysis through multiple interfaces.

The first release of Scout is focused on HL7 radiology reports. Future versions will incorporate DICOM, pathology reports, and extracted features.

```{admonition} New to Scout?
:class: tip
Start with the **[Quickstart](user/quickstart.md)** — a few-minute tour of
Analytics, Chat, and Notebooks.
```

## Documentation

* **[Using Scout](user/index.md)** — explore and analyze data: logging in, Analytics, Chat, Notebooks, and understanding what data you can see.
* **[Operating Scout](operate/index.md)** — deploy and run Scout: the Ansible inventory, air-gapped installation, report ingestion, and per-user data-authorization configuration.
* **[Customize Scout](customize/index.md)** — customize Scout: deploy your own playbooks and services into the Scout platform
* **[Reference](reference/index.md)** — the report data schema, backend architecture, and supporting database schemas.

* **Report Issues**: Submit issues on [GitHub](https://github.com/washu-tag/scout/issues)

```{toctree}
:hidden:
user/index
operate/index
customize/index
reference/index
```
