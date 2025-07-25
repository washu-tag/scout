# DCM4CHEE PACS

This chart defines the deployment of the minimum services of [dcm4chee-arc-light](https://github.com/dcm4che/dcm4chee-arc-light/wiki/Run-minimum-set-of-archive-services-on-a-single-host)
and is configured with Ansible as an optional part of a standard Scout installation.

## Known limitations

As of right now, the deployment only works when deployed at the root of the server, so it is not
compatible with running superset at the same time. Additionally, there may be some sort of persistent and intermittent connectivity
issue in attempting to communicate with the PACS over DIMSE. For example, in attempting to perform a C-ECHO twice, it may work once and then fail the next time like this:
```shell
$ echoscu -aec DCM4CHEE myurl 11112 -v
I: Requesting Association
I: Association Accepted (Max Send PDV: 16366)
I: Sending Echo Request (MsgID 1)
I: Received Echo Response (Success)
I: Releasing Association
$ echoscu -aec DCM4CHEE myurl 11112 -v
I: Requesting Association
F: Association Request Failed: 0006:031c TCP Initialization Error: Connection refused
```

## Configuration

The following variables are required to be set:
`dcm4chee_namespace`: Kubernetes namespace for all DCM4CHEE resources.
`dcm4chee_dir`: Base directory on the local file system to use for all volume mounts for DCM4CHEE pods.
`dcm4chee_dicom_port`: Port on which DIMSE services will be exposed.

The following variables all have default values if not provided in your inventory file:
`dcm4chee_timezone`: Timezone string to be included in DCM4CHEE containers. Defaults to `America/Chicago`.
`dcm4chee_arc_version`: Version of the [archive image](https://github.com/dcm4che-dockerfiles/dcm4chee-arc-psql) to deploy.
`dcm4chee_db_version`: Version of the [db image](https://github.com/dcm4che-dockerfiles/postgres-dcm4chee) to deploy.
`dcm4chee_ldap_version`: Version of the [Slapd image](https://github.com/dcm4che-dockerfiles/slapd-dcm4chee) to deploy.
`dcm4chee_wildfly_storage_size`: Size of the PV available for wildfly in the archive container. Defaults to `10Gi`.
`dcm4chee_storage_storage_size`: Size of the PV available for storage (primarily for DICOM) within archive container. Defaults to `10Gi`.
`dcm4chee_db_storage_size`: Size of the PV available for postgres within the database container. Defaults to `10Gi`.
`dcm4chee_openldap_storage_size`: Size of the PV available for openldap data within the Slapd container. Defaults to `10Gi`.
`dcm4chee_slapd_storage_size`: Size of the PV available for Slapd within the Slapd container. Defaults to `10Gi`.
`dcm4chee_db`: Name of the (postgres) database to use for DCM4CHEE. Defaults to `pacsdb`.
`dcm4chee_db_user`: Username for the database account. Defaults to `pacs`.
`dcm4chee_db_password`: Password for the database account. Defaults to `pacs`.

The following variables are fully optional and can be omitted entirely:
`dcm4chee_populate`: The path on the local disk to use for a job to send data to the PACS. If the variable is not defined,
the populate job will be skipped. Otherwise, it will be created as an async job to populate the PACS from the provided
directory.