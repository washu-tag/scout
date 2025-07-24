# Orthanc PACS

This chart defines the deployment of [Orthanc](https://www.orthanc-server.com/)
and is configured with Ansible as an optional part of a standard Scout installation.

The following variables are required to be set:
`orthanc_namespace`: Kubernetes namespace for all Orthanc resources.
`orthanc_dir`: Base directory on the local file system to use for all volume mounts for Orthanc pods.
`orthanc_dicom_port`: Port on which DIMSE services will be exposed.

The following variables all have default values if not provided in your inventory file:
`orthanc_image`: Docker image for Orthanc to deploy. Defaults to `jodogne/orthanc-plugins`.
`orthanc_version`: Version for Docker image defined in `orthanc_image`. Defaults to `latest`.
`orthanc_storage_size`: Size of the PV available for Orthanc. Defaults to `100Gi`.
`orthanc_username`: Username for the Orthanc account used in the UI and API. Defaults to `orthanc`.
`orthanc_password`: Password for the account in `orthanc_username`. Defaults to `orthanc`.

The following variables are fully optional and can be omitted entirely:
`orthanc_populate`: The path on the local disk to use for a job to send data to the PACS. If the variable is not defined,
the populate job will be skipped. Otherwise, it will be created as an async job to populate the PACS from the provided
directory.
