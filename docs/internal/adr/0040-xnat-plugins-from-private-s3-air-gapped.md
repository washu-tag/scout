# ADR 0040: XNAT Plugins from Private S3 on Air-Gapped Sites

**Date:** 2026-11
**Status:** Accepted
**Decision Owner:** TAG Team

## Context

Some XNAT plugins are published only to a private, IAM-protected S3 bucket. On an
air-gapped site, the only egress for build artifacts is the staging node's Nexus
(ADR 0017). XNAT plugins already reach the cluster that way: a `coordinates` plugin
resolves through the `scout-maven` group, and the role creates one Nexus proxy per
plugin `repo_url`.

A private bucket cannot fit that pattern. A Nexus proxy can authenticate to its remote
only with basic, NTLM or bearer-token auth, not AWS SigV4, so it cannot proxy a bucket
that requires IAM credentials. The XNAT chart's own `source: s3` install (an `aws s3 cp`
init container) needs direct S3 egress from the cluster and AWS keys in a cluster
Secret. That would mean a Squid allowlist entry, proxy wiring for the AWS CLI, and
long-lived credentials inside the gap, which defeats the Nexus-only egress model.

## Decision

The staging side fetches the jar and publishes it into Nexus. The cluster installs it as
an ordinary `coordinates` plugin.

- **A hosted Maven repo in Nexus.** The nexus role creates a `hosted` Maven repo
  (release version policy, write policy allow-once) and adds it to the `scout-maven`
  group. Any coordinate the group resolves can then come from it.
- **Plugins declare where to stage from.** A `coordinates` plugin in `xnat_plugins` may
  carry `source.stage_from: s3://bucket/key.jar`. Like every other plugin that the
  staging-run nexus role must see, it is set in `all.vars`.
- **A staging-side task does the transfer, once per coordinate.** On the staging node,
  which has egress, the task first checks whether the declared coordinate already exists
  in the hosted repo. If it does, the task does not contact S3 at all, so a staged
  release is fetched from S3 exactly once and every later run and pod start reads from
  Nexus. Only a missing coordinate is downloaded from `stage_from` and uploaded through
  the Nexus components API. Allow-once means a published release is never silently
  replaced.
- **The credentials stay on the controller.** The AWS credentials are vault-backed
  inventory variables. Ansible uses them on the control node to presign a short-lived
  GET URL for the object, and the staging node downloads that URL with `curl`. The
  staging node needs no AWS CLI, SDK or credentials.
- **An optional checksum pins the jar.** If `source.sha256` is set, the task checks it
  after downloading from S3, and the chart checks it again at install, as it does today.
  When the coordinate already exists, the task compares `sha256` against the checksum
  Nexus already stores for that asset, still without contacting S3, and fails on a
  mismatch. So a different jar published under an unchanged version, or a tampered
  bucket, is reported instead of ignored. Without `sha256`, an existing coordinate is
  trusted as is. To ship a new jar, bump the version in `coordinates`.

  To populate `sha256`, stream the object through the hash without saving it:

  ```
  aws s3 cp s3://bucket/path/my-plugin.jar - | sha256sum
  ```

  The task does not try to read a checksum from S3 metadata. S3 exposes a usable
  SHA-256 without a download only when the publisher uploaded with a SHA-256 checksum
  in a single part, and Scout does not control how plugins are published.
- **The XNAT deploy checks that staging happened.** Before the Helm install, the xnat
  role requests each `stage_from` plugin's artifact from `maven_proxy_url`, the same
  URL the chart's init container will use. If any are missing, the deploy fails before
  touching the cluster. The error names the missing coordinates and says to run
  `make install-staging` first. Without this check, a forgotten staging run would show
  up only as a failed init container in a pod that never starts.
- **The cluster side is unchanged.** The plugin renders as `source: coordinates` with
  `mavenUrl` set to `maven_proxy_url`. The AWS credentials never become a Kubernetes
  Secret, and the cluster never talks to S3.

This decision covers only air-gapped deployments. A `stage_from` plugin on a
non-air-gapped site is rejected at deploy time. Connected sites that need private S3
plugins would use the chart's native `source: s3` path, which is a separate change.

### GitOps (ADR 0031)

No special handling is needed. The Flux path does not install XNAT today. When it does,
the cluster side is just a coordinate resolved through the Nexus group, which Flux can
express as a site value. The staging-side upload belongs to the staging node's services,
which ADR 0031 keeps in Ansible's Layer 0.

## Consequences

- Private-bucket plugins work on air-gapped sites without cluster egress or cluster-held
  AWS credentials.
- The hosted repo is a general way to side-load jars into the air gap. That includes
  plugins too large for the `file` source's ~1 MiB Secret limit, if a later change lets
  `stage_from` take sources other than S3.
- Only release coordinates work, as for every coordinate plugin. To publish a new build,
  bump its version.
- Once a coordinate is staged, the S3 credentials are not used again. Rotating,
  expiring or revoking them breaks only the staging of new versions; existing
  deployments and re-runs keep working.

## Alternatives Considered

- **Nexus proxy over an anonymously readable bucket**, restricted by a bucket policy on
  source IP or VPC endpoint. This needs no code, but it replaces IAM with network policy
  for access control. Rejected where the bucket must stay IAM-private.
- **The chart's `source: s3` init container through Squid.** It puts AWS keys in the
  cluster and adds S3 to the egress allowlist. Rejected for the reasons in Context.
- **A presigned `url` plugin.** The init container fetches the jar again on every pod
  start, so restarts break once the URL expires. It also needs cluster egress.
