# ADR 0017: Air-Gapped Package Proxy

**Date**: 2026-03  
**Status**: Accepted  
**Decision Owner**: TAG Team

## Context

Scout's air-gapped deployment requires all software artifacts to be available without internet access on production nodes. Container images are already handled well by a Harbor pull-through proxy on the staging node. Other artifact types — Python/conda packages, JVM JARs, and system RPMs — each have ad-hoc solutions that create friction:

- **Python/conda packages** are baked into the Jupyter notebook image at build time. This produces large images, slow CI builds, and version lock-in. Users who need a different package version or a package that isn't pre-installed must request a new image build. There is no way for users to install packages on demand.
- **JVM JARs** (for Spark and Delta Lake) are downloaded from Maven Central and GitHub via hard-coded URLs in Dockerfiles. Version updates require image rebuilds, and stale versions accumulate CVE exposure.
- **System RPMs** (for K3s SELinux policies, NVIDIA drivers, etc.) are downloaded inside a container on the staging cluster, copied to the Ansible control node, transferred to each production node, placed in a temporary local repository, installed, and cleaned up. This multi-step process is time-consuming, fragile, and produces no reusable cache.

The common thread is a need for pull-through proxies that cache artifacts on first request and serve them to production nodes using standard package manager mechanisms — the same pattern Harbor already provides for container images.

### Jupyter User Agency

The most significant limitation of the current approach is that Jupyter notebook users cannot install packages themselves. Every Python and conda package must be selected at image build time and baked into the notebook image. This forces a one-size-fits-all environment that cannot accommodate diverse analytical workloads, prevents users from experimenting with new libraries, and shifts routine package management onto the platform team. A package proxy would allow users to install packages on demand from within their notebooks using standard `conda install` and `pip install` commands, with the proxy transparently caching packages from upstream repositories.

### Conda Constrains the Choice of Proxy

The choice of proxy tool is constrained by conda support. No well-maintained, truly open-source dedicated conda pull-through proxy exists. The conda-forge project is experimenting with OCI-based distribution (mirroring packages to container registries), but this is not mature enough to depend on today. Among multi-format proxy tools, only Nexus Repository Manager (CE and Pro editions) and JFrog Artifactory Pro support conda proxy repositories. Artifactory Pro is a commercial product ($6,000+/year). This makes Nexus CE the only viable free option for proxying conda packages in an air-gapped environment.

## Decision

**Deploy Sonatype Nexus Repository Manager (Community Edition) on the staging node alongside the existing Harbor registry to serve as a pull-through proxy for conda, PyPI, Maven, and RPM packages. Harbor continues to handle container images.**

Nexus will be configured with proxy repositories that cache packages from upstream sources on first request. Production nodes and Jupyter pods will be configured to resolve packages through Nexus using standard package manager configuration (`.condarc`, `pip.conf`, `dnf.conf`/yum repo files, Maven/Ivy settings). Users will not need to know about the proxy — their standard package management commands will work transparently.

### Why Nexus CE Alongside Harbor (Not Replacing It)

Harbor is a CNCF-graduated project purpose-built for container images, with features Nexus cannot match (vulnerability scanning, content trust, replication policies). It is already deployed and working. Keeping container images in Harbor also preserves Nexus's component headroom — Nexus CE has a cap of 40,000 total components, and container image layers would consume a significant portion of that budget. Separating concerns between two tools reduces the blast radius if either service has issues.

### Proxy Repositories

Nexus will host proxy repositories for the following upstream sources:

- **Conda**: conda-forge, conda defaults channel
- **PyPI**: pypi.org
- **Maven**: Maven Central (repo1.maven.org)
- **RPM/yum**: Rancher K3s, NVIDIA container toolkit, EPEL

Additional proxy repositories can be added in the future as needs arise. ML model proxying (e.g., HuggingFace) is out of scope for this effort; existing approaches (NFS pre-staging per ADR 0008) continue to apply.

### Service-Mode Variables (per ADR 0011)

Following the service-mode pattern from ADR 0011, we introduce a `package_proxy_mode` variable and per-format proxy URL variables:

```yaml
# Mode variable
package_proxy_mode: none    # Options: none, nexus, external

# Nexus connection (used when mode is 'nexus'; TLS required per ADR-0015)
nexus_host: "{{ staging_hostname }}:8081"  # derived from staging node

# Per-format proxy URLs (computed from Nexus endpoint when mode is 'nexus')
conda_channel_alias: "https://{{ nexus_host }}/repository"
pip_proxy_url: "https://{{ nexus_host }}/repository/pypi-proxy/simple"
maven_proxy_url: "https://{{ nexus_host }}/repository/maven-central/"
yum_proxy_url: "https://{{ nexus_host }}/repository/"
```

#### Conda Repository Naming Convention

Nexus conda proxy repositories are named to match the upstream channel names that users already know (`conda-forge`, `defaults`). The `.condarc` sets `channel_alias` to the Nexus base URL (`https://nexus_host/repository`), so when a user references `conda-forge`, conda constructs `{channel_alias}/conda-forge/{subdir}` — which maps directly to the Nexus `conda-forge` repository. The special `defaults` channel is handled via `default_channels`, pointing to the Nexus `defaults` repository (which proxies `repo.anaconda.com/pkgs/main`).

This means users interact with standard channel names (`conda-forge`, `defaults`) and standard commands (`conda install -c conda-forge pandas`) without awareness of the proxy.

#### Modes

The three modes are:

- **`none`** (default): No proxy configured. Packages resolve directly from upstream repositories. This is appropriate for internet-connected deployments or environments where package access is not needed.
- **`nexus`**: Nexus CE deployed on the staging node. Proxy URLs are computed automatically from `nexus_host`. Operators only need to set `package_proxy_mode: nexus` in inventory; all URLs derive from the staging node's address.
- **`external`**: Operator-provided proxy. Set proxy URLs (`conda_channel_alias`, `pip_proxy_url`, etc.) directly in inventory. This supports environments where an institutional package proxy already exists.

The per-format URL granularity allows mixing sources — for example, using an institutional PyPI mirror while proxying conda through Nexus.

### Staging Certificate Trust (per ADR 0016)

Nexus runs on the staging node behind TLS. When the staging node uses a self-signed certificate, the certificate must be distributed to clients that contact Nexus. For Jupyter pods, this means mounting the staging CA certificate and configuring conda and pip to trust it. For production nodes running dnf, the certificate is added to the system trust store. This follows the patterns established in ADR 0016.

### Jupyter Conda Environments

Conda environments created by users will be stored in each user's persistent home directory. This means environments survive across notebook server restarts and users have full control over their package versions.

The trade-off is that packages cannot be shared across user spaces — if ten users install the same package, it is stored ten times. This is an acceptable cost for the simplicity and isolation it provides. Each user's environment is independent, which avoids version conflicts and simplifies troubleshooting.

## Changes Required

### Ansible Roles and Playbooks

- A new Ansible role to deploy and configure Nexus on the staging node, integrated into the existing staging playbook. With both Harbor and Nexus requiring ingress, Traefik is now always deployed on staging (the previous `harbor_expose_type: nodePort` mode is removed). This supersedes the conditional Traefik deployment described in ADR 0007.
- The Jupyter role will be updated to inject proxy configuration (`.condarc` and `pip.conf`) into notebook pods so that conda and pip resolve packages through Nexus transparently
- RPM installation on production nodes will be simplified: instead of the current multi-step process of downloading packages in a container, copying archives between nodes, and installing from a temporary local repository, production nodes will have standard yum repository configuration pointing to Nexus proxy repositories, and packages will be installed using ordinary `dnf install` commands
- The `spark-defaults.conf` template and related configuration may be updated to resolve Spark JARs from the Maven proxy at startup rather than downloading them at image build time, though this is a potential future optimization rather than a requirement of the initial implementation

### Jupyter Image

The package proxy enables a significant simplification of the Jupyter notebook image. Packages that are currently baked into the image at build time — data science libraries, ML frameworks, Scout-specific connectors — can be moved out of the image and installed by users at runtime through the proxy. This results in a smaller base image, faster builds, and the elimination of purpose-built image variants (e.g., the current separate embedding image). A default environment specification can be shipped alongside the Quickstart notebook so that users have a working set of packages without manual setup.

## Alternatives Considered

### Proxy Tool Landscape

We evaluated both multi-format proxy tools and single-purpose alternatives. The key constraint is conda support — see "Conda Constrains the Choice of Proxy" in the Context section.

#### Multi-Format Tools

| Tool | Conda | PyPI | Maven | RPM/yum | Free | Pull-Through |
|------|-------|------|-------|---------|------|-------------|
| **Nexus CE** | Yes | Yes | Yes | Yes | Yes* | Yes |
| **Pulp** | **No** | Yes | Yes | Yes | Yes | Yes |
| **Artifactory Pro** | Yes | Yes | Yes | Yes | No ($6k+/yr) | Yes |
| **Artifactory OSS** | No | No | Yes | No | Yes | Yes |
| **ProGet** | Yes | Yes | Yes | Yes | Free tier† | Yes |
| **Harbor** | No | No | No | No | Yes | Yes |
| **Squid** | HTTP‡ | HTTP‡ | HTTP‡ | HTTP‡ | Yes | Yes |

\* Nexus CE has usage caps: 40,000 total components and 100,000 requests/day. When exceeded, new component additions are blocked but reads of already-cached content continue to work.

† ProGet is proprietary, not open source. The free edition has no feed, package, or user limits but restricts connector filters, metadata caching, and security API to paid tiers.

‡ Squid caches HTTP responses generically with SSL bumping. Works for package files but is not format-aware (no metadata intelligence).

#### Single-Purpose Tools

| Tool | Format | License | Pull-Through | Notes |
|------|--------|---------|-------------|-------|
| **devpi** | PyPI | MIT | Yes | Best-in-class PyPI proxy; redundant if Nexus is deployed |
| **Reposilite** | Maven | Apache 2.0 | Yes | Lightweight Maven proxy; redundant if Nexus is deployed |
| **Quetz** | Conda | BSD-3 | Yes | Only dedicated conda proxy; stagnating (mamba-org) |
| **proxpi** | PyPI | Apache 2.0 | Yes | Lightweight, CI-focused; too minimal for persistent use |
| **nginx cache** | Any HTTP | BSD-2 | Yes (generic) | Generic HTTP caching; same metadata freshness issues as Squid |

Nexus CE is the only free tool that supports pull-through proxying for all four package formats Scout needs (conda, PyPI, Maven, RPM). The single-purpose tools are individually strong for their respective formats, but no well-maintained conda proxy exists to fill the gap that Nexus covers. If conda OCI distribution matures in the future (allowing conda packages to be served from a container registry), the single-purpose approach would become more viable.

### Squid HTTP Proxy (SSL Bumping)

A generic HTTP caching proxy like Squid can cache package downloads from any HTTPS source using SSL bumping (MITM decryption). This is a universal approach that requires no per-format repository configuration.

**Pros:**
- Universal — works for any HTTP-based package manager without format-specific setup
- Lightweight resource footprint
- No usage caps
- If Squid were already deployed on staging for other purposes (e.g., forward proxy or traffic inspection), adding package caching would be incremental configuration

**Cons:**
- Requires SSL bumping to cache HTTPS content, which means generating and distributing a CA certificate to all clients
- Not format-aware — caches HTTP responses without understanding package metadata. Stale metadata (e.g., conda's `repodata.json`, yum's `repomd.xml`) causes real operational problems that require careful per-format TTL tuning
- No web UI, package browsing, or cleanup policies beyond LRU eviction
- Does not support conda's `channel_alias` rewriting, so user-facing configuration is less transparent

**Verdict:** Compelling if already deployed with SSL bump enabled, but deploying Squid solely for package caching is harder to justify when Nexus handles HTTPS natively and understands package metadata.

### Pulp (RPM-Focused)

Pulp is an open-source repository management tool with excellent RPM support (content versioning, rollback, fine-grained sync policies). It also supports PyPI and Maven but does not support conda.

**Pros:**
- Best-in-class RPM repository management
- No usage caps
- Fully open source

**Cons:**
- No conda support, so it cannot replace Nexus for the primary use case
- Requires PostgreSQL, Redis, and worker processes (significant operational overhead)
- Deploying Pulp alongside Nexus for only three RPM repositories is disproportionate to the need

**Verdict:** If RPM proxying needs grow substantially (e.g., proxying full OS base repositories) or if Nexus's component cap becomes a constraint, Pulp could take over RPM responsibilities to reduce pressure on Nexus. Not justified for the current scope.

### Nexus CE as Sole Proxy (Replacing Harbor)

Nexus CE supports Docker registry proxy repositories and could theoretically replace Harbor for container images as well.

**Pros:**
- Single tool for all artifact types

**Cons:**
- The 40,000 component cap becomes a serious risk when container image layers (each counted as a component) are added to the package workload
- Harbor has superior container-specific features (vulnerability scanning, content trust, CNCF governance)
- Replacing a working Harbor deployment is unnecessary churn

**Verdict:** Not recommended. Keeping Harbor for containers preserves component headroom in Nexus and retains Harbor's container-specific capabilities.

## Consequences

### Positive

- Users can install conda and pip packages on demand from within Jupyter notebooks without platform team intervention
- The Jupyter notebook image can be significantly reduced in size by removing baked-in packages, resulting in faster builds and pulls
- RPM installation on production nodes is simplified from a multi-step artifact-transfer process to standard `dnf install` commands
- Multiple purpose-built Jupyter image variants can be consolidated into a single base image
- Package versions are no longer locked at image build time — users and administrators can update independently
- The proxy cache on staging means packages are downloaded from the internet only once, then served locally to all consumers
- Maven proxy repositories open the possibility of resolving Spark JARs at startup rather than baking them into images

### Negative

- Nexus CE's 40,000 component cap requires monitoring; if usage grows beyond expectations, cleanup policies or offloading RPMs to another tool (e.g., Pulp) may be necessary
- Nexus is JVM-based and requires approximately 4–5 GB of RAM on the staging node
- User conda environments stored in individual home directories result in duplicated package storage across users
- Users' first-run experience changes: instead of a pre-configured environment, they may need to create or activate a conda environment before their notebooks work (mitigated by shipping a default environment specification)
- The staging node becomes a single point of failure for package installation (though not for already-installed packages); this is consistent with its existing role as a single point of failure for container image pulls via Harbor
- Sonatype's strategic direction for Nexus CE (reduced component caps, cloud-first focus) introduces some long-term vendor risk; ProGet free edition is a potential fallback if CE becomes untenable

### Operational

- **Monitoring**: Track Nexus component count against the 40,000 cap; configure cleanup policies to prune unused cached packages
- **Staging certificate**: When the staging node uses a self-signed certificate, distribute it to Jupyter pods and production nodes per ADR 0016
- **Storage**: Nexus proxy cache requires persistent storage on the staging node; size depends on the breadth of packages cached
- **User PVC sizing**: User home directories may need larger persistent volumes to accommodate conda environments (ML stacks can be several GB)

## Security Considerations

### Threat Model

The package proxy introduces a qualitatively different risk from the existing Harbor pull-through proxy. Container images pulled through Harbor are curated by the platform team. Packages installed through Nexus are chosen by end users — and in the primary use case (Jupyter notebooks), the user runs `pip install` or `conda install` for arbitrary packages from PyPI and conda-forge. If a user inadvertently installs a malicious or compromised package (via typosquatting, supply chain attack, or compromised maintainer), that code executes inside the Jupyter notebook pod with the same access as the user's own code.

The threat we are concerned with is **inadvertent PHI egress** — a malicious package exfiltrating data from the data lake. We assume the user is not a malicious actor; the risk is that a legitimate user installs a package that contains malicious code they did not author.

A compromised package running in a notebook pod has access to the full data lake via Trino and MinIO. The question is whether it can transmit that data outside the air gap.

### Existing Mitigations

Several layers of defense already constrain what malicious code in a notebook pod can do:

- **Kubernetes network policies** restrict notebook pod egress to specific internal services (MinIO, Hive Metastore, Trino, Ollama) on specific ports. Arbitrary outbound connections from the pod are blocked.
- **Firewall between production and staging** limits traffic to ports 80 and 443 (Traefik ingress). The Nexus egress rule should be scoped to these ports only, preventing malicious code from probing other services on the staging node.
- **CoreDNS configuration** blocks resolution of external domains from within the production cluster. Only specific known domains are forwarded to upstream DNS. This prevents DNS-based data exfiltration from within the pod.
- **Content Security Policy** (ADR 0012) on the Traefik ingress restricts browser-side network requests. `connect-src 'self'` prevents JavaScript running in the JupyterLab frontend from making `fetch()` or `XMLHttpRequest` calls to external servers.

### Residual Risks

**Browser-side exfiltration via JupyterLab frontend extensions.** A pip-installed package can bundle a JupyterLab frontend extension that auto-activates via Python entry points, bypassing the disabled Extension Manager UI. Frontend extensions execute as JavaScript in the user's browser — which is outside the air gap and may have unrestricted internet access. A malicious frontend extension could read notebook cell outputs (including PHI from query results) from the DOM or intercept kernel WebSocket messages containing execution results.

The CSP `connect-src` directive blocks `fetch()` to external origins, but the current policy allows WebSocket connections to any host (`ws:` and `wss:` without host restriction). A malicious extension could open a WebSocket to an attacker-controlled server and stream data out through the user's browser. Tightening `connect-src` to scope WebSocket connections to the JupyterHub origin (e.g., replacing bare `ws: wss:` with `ws://<external_url> wss://<external_url>`) would close this gap.

**Data encoded in package lookup requests.** Malicious code could encode small amounts of data in fabricated package names (e.g., `pip install <encoded-data>`), causing Nexus to forward the lookup to PyPI. This is a low-bandwidth, low-practicality vector — the attacker would need to monitor failed lookup requests on a public package index — but it exists as a theoretical channel. Monitoring Nexus audit logs for unusual volumes of failed package lookups would detect this pattern.

### Recommended Mitigations

1. **Tighten CSP WebSocket scope**: Restrict `ws:` and `wss:` in `connect-src` to the Scout deployment origin rather than allowing connections to arbitrary hosts. This closes the browser-side exfiltration path through frontend extensions.
2. **Scope the Nexus egress rule**: The Kubernetes network policy allowing notebook pods to reach the staging node should be limited to ports 80/443 only, matching the firewall rules.
3. **Nexus audit logging**: Consider monitoring Nexus request logs for anomalous patterns (high volumes of failed lookups, unusual package names) as an additional detection layer.

## Related

- **ADR 0008**: Ollama Model Distribution in Air-Gapped Environments — NFS pre-staging pattern for ML models
- **ADR 0011**: Deployment Portability via Layered Architecture — service-mode variable pattern
- **ADR 0012**: Security Scan Response and Hardening — global CSP and security headers middleware
- **ADR 0016**: Staging Node Certificate Distribution — TLS trust for staging-hosted services
