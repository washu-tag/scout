# DCM4CHEE PACS

This chart defines the deployment of the minimum services of [dcm4chee-arc-light](https://github.com/dcm4che/dcm4chee-arc-light/wiki/Run-minimum-set-of-archive-services-on-a-single-host)
and is configured with Ansible as an optional part of a standard Scout installation.

## HTTP routing (subdomain, not subpath)

The dcm4chee archive UI/API only works when served from the root path (`/`) of a host —
it cannot be reverse-proxied under a subpath prefix (e.g. `<host>/dcm4chee`). Scout therefore
gives it its own subdomain, `dcm4chee.<server_hostname>`, rather than a subpath of the main
host. This avoids the previous conflict with Superset (which has the same root-only constraint)
and is the same approach used for Orthanc (`orthanc.<server_hostname>`). The wildcard
`*.<server_hostname>` TLS cert (see `scout_common`) already covers the subdomain, and the app's
own UI path (`/dcm4chee-arc/ui2`) lives under that subdomain root.

## Known limitations

There may be some sort of persistent and intermittent connectivity
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
`dcm4chee_expose_management_console`: When `true` (default), serve the WildFly admin console through the ingress at `https://dcm4chee.<server_hostname>/console` and point the Archive UI's "Administration Console" link there (via `UI_MANAGEMENT_URL`), instead of the WildFly default non-standard port `:9993/console`. HAL's `/console` and `/management` paths are routed to the management **HTTPS** port via a dedicated `arc-management` service + a Traefik `ServersTransport` (the management HTTP port always 302-redirects to `:9993` absolutely; the HTTPS port serves relative redirects). **Note:** the image seeds no management user, so the console prompts for `ManagementRealm` credentials that don't exist until you create one — this flag only fixes reachability. **Security:** the dcm4chee ingress is not behind oauth2-proxy, so this exposes the management console on the public subdomain — fine for dev, gate or set `false` for internet-facing deployments.
`dcm4chee_management_https_port`: WildFly management HTTPS port the console/management ingress paths target. Defaults to `9993`.
`dcm4chee_http_proxy_address_forwarding`: Sets `HTTP_PROXY_ADDRESS_FORWARDING` so WildFly trusts `X-Forwarded-*` from the ingress. Defaults to `true`.
`dcm4chee_redirect_https_port`: Sets `REDIRECT_HTTPS_PORT` — the port WildFly uses for HTTPS redirects. Defaults to `443` (WildFly's own default is `8443`, a non-standard port) so no UI redirect lands on a non-standard port.

The following variables are fully optional and can be omitted entirely:
`dcm4chee_dicom_service_load_balancer_class`: `loadBalancerClass` for the DIMSE `LoadBalancer` service. Needed on cloud providers (e.g. `service.k8s.aws/nlb`); leave unset on-prem, where K3s ServiceLB binds the port on every node.
`dcm4chee_dicom_service_annotations`: Annotations for the DIMSE `LoadBalancer` service (cloud-provider LB tuning). Defaults to `{}`.
`dcm4chee_ui_management_url`: Override the HTTPS URL the Archive UI links to for the admin console. Defaults to `https://dcm4chee.<server_hostname>/console` (only used when `dcm4chee_expose_management_console` is `true`).
`dcm4chee_populate`: The path on the local disk to use for a job to send data to the PACS. If the variable is not defined,
the populate job will be skipped. Otherwise, it will be created as an async job to populate the PACS from the provided
directory.