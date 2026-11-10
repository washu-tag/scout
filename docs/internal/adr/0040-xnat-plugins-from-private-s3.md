# ADR 0040: XNAT Plugins from Private S3

**Date:** 2026-11
**Status:** Accepted
**Decision Owner:** TAG Team

## Context

Some XNAT plugins are published only to a private, IAM-protected S3 bucket that a
static AWS access key can read. Scout has to install them on connected and
air-gapped sites, and an operator should declare such a plugin the same way on
both.

The XNAT chart can install a plugin from S3 itself. A `source: s3` plugin gets an
init container that runs `aws s3 cp` with credentials from a Secret. That works only
where the cluster can reach S3.

An air-gapped cluster can't reach S3. Its only route to build artifacts is the
staging node's Nexus (ADR 0017). A Nexus proxy can't front a private bucket,
because a proxy repository authenticates to its remote with basic, NTLM or
bearer-token auth, not AWS SigV4. The staging node does have internet access, and
the Ansible controller holds the inventory's credentials.

## Decision

**One declaration.** An S3 plugin is an `xnat_plugins` entry with `source.type: s3`
and `source.s3_uri: s3://bucket/key.jar`. The bucket's credentials are three
vault-encrypted variables: `xnat_plugin_s3_region`, `xnat_plugin_s3_access_key_id`
and `xnat_plugin_s3_secret_access_key`. All of it goes in `all.vars`, as every
plugin does, so the staging run sees it. The role picks the route from
`air_gapped`, as it does for coordinate plugins.

**Connected sites: the chart downloads the jar from S3.** The xnat role puts the
keys in a Secret and renders the plugin as `source: s3`, with the region and the
Secret as the chart's `pluginInstaller.s3` settings. Every pod start downloads the
jar again.

**Air-gapped sites: the staging run copies the jar into Nexus, and the cluster
installs that copy.**

- When any S3 plugin is declared, the nexus role creates a raw hosted repo,
  `xnat-plugins`, with an allow-once write policy.
- On `make install-staging`, the role checks each S3 plugin for `<bucket>/<key>`
  in that repo. If it is missing, Ansible presigns a short-lived GET URL on the
  controller (the `s3_presigned_url` filter). The staging node downloads that URL
  and PUTs the jar to the repo at the same path. The staging node needs no AWS
  tooling or credentials, and a jar already in Nexus is never fetched from S3
  again.
- The xnat role renders the plugin as `source: url` at that Nexus path, with the
  staging CA the installer uses for every Nexus fetch. The cluster never holds AWS
  credentials and never talks to S3.
- Before the Helm install, the xnat role requests every plugin the init containers
  will fetch from Nexus. It fails, naming the missing plugins, and points to
  `make install-staging`.
- S3 plugins on an air-gapped site require `package_proxy_mode: nexus`.

**A released S3 key is never overwritten.** As with Maven releases, each new build
goes under a new key, usually with the version in it. Nothing enforces this. If a
key were overwritten, a connected site would install the new object on its next pod
start, while an air-gapped site would keep the copy it made first. A bad copy is
replaced by deleting it in Nexus and running `make install-staging` again.

**No checksum pinning.** S3 plugins don't take `sha256`. The chart's `source: s3`
doesn't check one, and Scout pins none of its other plugins either. Integrity rests
on TLS and on the bucket's access control.

### GitOps (ADR 0031)

The Flux path doesn't install XNAT yet. When it does, each site's XNAT values will
carry the chart's own form of the entry. On a connected site that is `source: s3`,
with the keys in the site's secrets. On an air-gapped site it is `source: url` at
the Nexus path. The copy into Nexus is a staging-node service, which ADR 0031 keeps
in Ansible's Layer 0, so `make install-staging` keeps doing it. If a single
declaration across modes matters under Flux, a chart setting that redirects
`source: s3` plugins to a mirror would provide it.

## Consequences

- Operators declare a private-S3 plugin the same way on connected and air-gapped
  sites.
- On connected sites, the AWS keys are in a cluster Secret, and every XNAT pod
  start depends on S3 and on the keys being valid. The installer reads the Secret
  at each start, so rotating the keys needs only an inventory change and a
  redeploy, without restarting XNAT.
- On air-gapped sites, the keys stay on the controller and are used only to copy a
  jar that isn't in Nexus yet. Rotating or revoking them affects only plugins that
  haven't been copied.
- On air-gapped sites, adding an S3 plugin takes `make install-staging` before the
  XNAT deploy, the same order as adding a coordinate plugin from a new repository.
- On a connected site, S3 plugins can't be combined with the dev side-load
  (`xnat_dev_war`, `xnat_dev_plugins`). The chart has one S3 endpoint and
  credential, and the side-load points it at MinIO.

## Alternatives Considered

- **The chart's `source: s3` on air-gapped sites too, through Squid.** This would
  put AWS keys in the cluster and add S3 to the egress allowlist, which defeats the
  Nexus-only egress model.
- **A copy in in-cluster MinIO on every on-prem site, pushed from the controller.**
  It gives one route for connected and air-gapped on-prem sites, and the chart
  reads MinIO through `source: s3`. But XNAT would need a dedicated bucket and a
  read-only MinIO user, not the lake's root credentials. The controller would need
  a way to push into the cluster, and the role would have to provide the write-once
  guarantee Nexus has built in. Air-gapped XNAT still fetches its other plugins
  from Nexus, so this adds a second artifact source instead of replacing one. It
  is also an Ansible step on every deploy, which has nowhere to run after the
  GitOps cutover. MinIO doesn't exist on AWS deployments.
- **A Nexus hosted Maven repo, with a Maven coordinate chosen per jar.** The jar
  would install as an ordinary `coordinates` plugin through the `scout-maven`
  group. But every S3 jar would need an invented coordinate, and the upload would go
  through Nexus's multipart components API instead of a plain PUT. A raw repo
  addresses the jar by its S3 path, so the S3 URI is the whole declaration.
- **A `sha256` pin per jar,** checked after the download from S3, against Nexus's
  stored checksum on later runs, and by the chart at install. It guards against a
  different jar under the same key, which the bucket's access control and the
  no-overwrite rule already cover. The chart's `source: s3` doesn't check it, so it
  wouldn't protect connected sites anyway.
- **A Nexus proxy over an anonymously readable bucket,** restricted by a bucket
  policy on source IP or VPC endpoint. It needs no code, but it replaces IAM with
  network policy for access control. Rejected where the bucket must stay
  IAM-private.
- **A presigned `url` plugin.** The init container would download the jar again on
  every pod start, so restarts would break once the URL expired. It also needs
  cluster egress.
