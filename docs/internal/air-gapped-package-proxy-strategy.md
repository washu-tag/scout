# Air-Gapped Package Proxy Strategy

*Date: 2026-03-12*

## Problem Statement

Scout's air-gapped deployment model requires all software artifacts -- container images, Python packages, JVM JARs, and system RPMs -- to be available without internet access on production nodes. Today, each artifact type has its own ad-hoc solution:

| Artifact Type | Current Approach | Pain Points |
|---------------|-----------------|-------------|
| **Container images** | Harbor pull-through proxy on staging | Works well; already deployed |
| **Python/conda packages** | Baked into Jupyter images at build time | Huge images, slow CI, version lock-in, no user agency |
| **JVM JARs** | Hard-coded URLs in Dockerfiles | Version drift, manual updates, CVE exposure |
| **System RPMs** | K8s Job downloads on staging, tar+ship to nodes | Time-consuming, no caching, brittle, wasteful |
| **ML models** | NFS pre-staging (Ollama), baked into images (HuggingFace) | Manual process, no caching, limited to pre-selected models |

The common thread: **we need pull-through proxies that cache artifacts on first request and serve them transparently to production nodes.** Harbor already does this for container images. This report investigates extending that pattern to Python/conda packages, Maven artifacts, RPM repositories, and ML model files.

### Jupyter-Specific Problems

The most acute pain is in the Jupyter notebook images:

1. **Image size and build time** slow CI, releases, and deployment
2. **Version lock-in**: Users who need different Python/package versions require a new image build
3. **Multiple image variants** (standard, embedding) multiply the maintenance burden
4. **Stale dependencies**: Spark, JARs, and Python packages have hard-coded versions that trip CVE scanners and are difficult to update
5. **No user agency**: Users cannot install packages they need without admin intervention

### RPM-Specific Problems

Scout's `scout_common` role has a sophisticated but cumbersome RPM management framework (`ensure_packages.yaml` → `download_rpms_via_job.yaml` → `install_rpms_from_artifacts.yaml`). Currently used for:

- **K3s SELinux packages** (`k3s-selinux`, `container-selinux`) from Rancher repos
- **NVIDIA container toolkit** (`nvidia-container-toolkit` and dependencies) from NVIDIA repos
- **python3-kubernetes** from EPEL

The workflow for each: spin up a Rocky Linux 9 K8s Job on staging → `dnf download --resolve` → `kubectl cp` to control node → `fetch` to Ansible host → copy to production → create temp local repo → `dnf install`. This works, but:

- **No caching**: Packages are downloaded fresh on every playbook run
- **Wasteful**: `--resolve` downloads all transitive dependencies even if already installed
- **Brittle**: Depends on staging K8s cluster being available, Rocky Linux image being pullable, and temp directory management across three nodes
- **Slow**: Multiple network hops (upstream → staging pod → control node → production node)
- **Not native**: Production nodes cannot simply run `dnf install <package>` -- the entire framework exists to work around the lack of a repo proxy

With an RPM proxy on staging, production nodes could use standard dnf repository configuration, and the entire `download_rpms_via_job.yaml` / `install_rpms_from_artifacts.yaml` framework could be retired.

---

## Current State: Jupyter Images

Two Dockerfiles exist, both based on `quay.io/jupyter/pyspark-notebook:spark-3.5.3`:

**Standard notebook** (`helm/jupyter/notebook/Dockerfile`):
- Installs 25+ pip packages (torch, transformers, scikit-learn, delta-spark, trino, etc.)
- Downloads 8 JARs into `$SPARK_HOME/jars/` (Delta Lake, Hadoop AWS, AWS SDK, smolder)
- Copies sample notebooks

**Embedding notebook** (`helm/jupyter/embedding-notebook/Dockerfile`):
- Everything from standard, plus embedding/ML packages (deepspeed, FlagEmbedding, sentence-transformers, faiss-gpu)
- Installs `uv` for faster package installation
- Installs `nb_conda_kernels` so user-created conda envs auto-appear as Jupyter kernels
- Configures `envs_dirs` in `.condarc` so user envs persist in the home PVC

The embedding image already has the beginnings of a user-managed-environments approach (`nb_conda_kernels` + persistent `envs_dirs`). The standard image does not.

### Spark and Data Lake Access

Users access the Delta Lake via PySpark:

```python
from pyspark.sql import SparkSession
spark = SparkSession.builder.appName("scout").enableHiveSupport().getOrCreate()
df = spark.read.table("reports")
```

Spark requires the base `pyspark-notebook` image (JVM + PySpark), Delta Lake JARs, Hadoop/S3 JARs, the custom `smolder` JAR, and runtime Spark configuration via ConfigMap. Users also access Trino directly via the `trino` Python package for SQL queries without Spark.

### JARs: A Specific Pain Point

The 8 JARs downloaded in the Dockerfile have hard-coded versions and URLs:

| JAR | Version | Notes |
|-----|---------|-------|
| delta-spark | 3.3.0 | Must match Delta Lake version |
| delta-storage | 3.3.0 | Must match Delta Lake version |
| hadoop-aws | 3.2.2 | Very old; Spark 3.5.3 ships with Hadoop 3.3.6 |
| hadoop-common | 3.2.2 | Same; version mismatch with base image |
| hadoop-hdfs | 3.2.2 | Same |
| antlr4-runtime | 4.9.3 | Transitive dependency |
| aws-java-sdk-bundle | 1.12.793 | AWS SDK v1; pinned |
| smolder | 0.1.0-SNAPSHOT | Custom; GitHub release |

These cannot be updated without manual testing because version incompatibilities between Spark, Delta, and Hadoop can cause silent failures.

---

## Tool Landscape

### Format Support Matrix

#### Multi-Format Tools

| Tool | Conda | PyPI | Maven | RPM/yum | HuggingFace | Containers | Free | Pull-Through |
|------|-------|------|-------|---------|-------------|------------|------|-------------|
| **Nexus CE** | Yes | Yes | Yes | Yes | Yes‡ | Yes | Yes* | Yes |
| **Pulp** | **No** | Yes | Yes | Yes | Plugin§ | Yes | Yes | Yes |
| **Artifactory Pro** | Yes | Yes | Yes | Yes | No | Yes | No ($6k+/yr) | Yes |
| **Artifactory OSS** | No | No | Yes | No | No | No | Yes | Yes |
| **ProGet** | Yes | Yes | Yes | Yes | No | Yes | Free tier† | Yes |
| **Harbor** | No | No | No | No | No | Yes | Yes | Yes |
| **Squid** | HTTP‖ | HTTP‖ | HTTP‖ | HTTP‖ | **No** | No | Yes | Yes |

\* Nexus CE has usage caps: **40,000 total components** and **100,000 requests/day**. When exceeded, new component additions are blocked.

† ProGet is proprietary, not open source. Free edition has no feed/package/user limits but restricts connector filters, metadata caching, and security API to paid tiers.

‡ Nexus CE HuggingFace proxy works for Git LFS-backed models but is at risk from HuggingFace's migration to Xet storage. No deduplication (cache bloat). Proxy only -- no hosted repos.

§ Pulp's `pulp_hugging_face` plugin (v0.1.0, Aug 2025) is very early stage. Supports models, datasets, and Spaces. Requires full Pulp deployment.

‖ Squid caches HTTP responses generically with SSL bumping. Works for package files but not format-aware (no metadata intelligence). Cannot cache HuggingFace (Git LFS signed URLs defeat URL-based caching).

#### Single-Purpose Tools

| Tool | Format | License | Pull-Through | K8s Story | Maintenance |
|------|--------|---------|-------------|-----------|-------------|
| **devpi** | PyPI | MIT | Yes | Community chart | Very active (6.19.x, Feb 2026) |
| **proxpi** | PyPI | Apache 2.0 | Yes | Docker only | Active, small community |
| **pypiserver** | PyPI | MIT/zlib | No (hosting only) | Helm chart | Active |
| **bandersnatch** | PyPI | AFL 3.0 | No (mirror tool) | Batch job | Active (PyPA project) |
| **Reposilite** | Maven | Apache 2.0 | Yes | Official Helm chart | Very active (3.5.x, 1.7k stars) |
| **Quetz** | Conda | BSD-3 | Yes | No chart, needs PG | Stagnating (mamba-org) |
| **Olah** | HuggingFace | MIT | Yes (+ offline mode) | Docker only | Active |
| **nginx cache** | Any HTTP | BSD-2 | Yes (generic) | Standard | N/A (infrastructure) |

### Detailed Assessments

#### Sonatype Nexus Repository Community Edition

The only free tool that covers **all five** artifact types (conda, PyPI, Maven, RPM, containers). Mature, well-documented, Helm chart available.

**Strengths:**
- True pull-through proxy for every format Scout needs
- Single tool, single deployment, single UI
- Official Helm chart for Kubernetes
- Well-established in enterprise environments

**Critical limitation -- usage caps:**
- 40,000 total components across all repositories
- 100,000 requests per day
- Each Docker image layer counts as a component
- RPM repos (RHEL, EPEL) can have tens of thousands of packages
- Conda-forge has hundreds of thousands of packages
- With pull-through (only caching what's requested), you might stay under 40K for a while, but a moderately active environment will likely hit this ceiling

**Resource requirements:** ~4-5 GB RAM (JVM-based), 4 CPU cores recommended

**Helm chart situation:**

Sonatype's Helm charts are in a transitional state that complicates Nexus CE deployment on Kubernetes:

- **Legacy chart** (`sonatype/nexus-repository-manager` from `helm3-charts`): **Deprecated and vulnerable.** The chart tops out at Nexus 3.64.0, which is affected by [CVE-2024-4956](https://support.sonatype.com/hc/en-us/articles/29416509323923) -- a critical unauthenticated path traversal vulnerability that allows attackers to read arbitrary files on the system. Sonatype recommends upgrading to Nexus 3.68.1 or later. The chart will not receive updates.

- **Current chart** (`sonatype/nxrm-ha` from `helm3-charts`): The only Sonatype-supported chart going forward (currently at appVersion 3.90.1). Requires an external PostgreSQL database and a **Nexus Pro license** -- it hardcodes `-Dnexus.licenseFile=${LICENSE_FILE}` in the StatefulSet template, which causes CE to fail at startup. A [community workaround](https://github.com/sonatype/nexus-public/issues/926) patches this, but vendoring and maintaining a fork of a Pro-oriented chart adds complexity.

Since Scout needs the free CE edition, neither official chart works out of the box. There are three viable paths:

**Path 1: Override the image tag on the deprecated chart**

The deprecated chart is very simple -- it creates a Deployment, Service, PVC, and ServiceAccount with no version-coupled logic. Setting `image.tag=3.90.1` (or any current Nexus version) works with two minor adjustments:

- Replace the JVM flag `-XX:+UseCGroupMemoryLimitForHeap` (removed in newer JDKs) with `-XX:+UseContainerSupport` or remove it entirely
- Remove `nexus.scripts.allowCreation` from properties if using the override (removed in Nexus 3.70+)

*Pros:* Minimal effort, immediate CVE remediation, no new dependencies.
*Cons:* No chart-level updates ever. If Sonatype changes the image's data path, default port, or UID in a future release, you must adjust Helm values manually. Uses an embedded database (OrientDB/H2) rather than PostgreSQL.

**Path 2: Vendor and patch the `nxrm-ha` chart for CE**

Fork the `nxrm-ha` chart into the Scout repo (e.g., `helm/nexus/`) and apply the license-conditional patch from [sonatype/nexus-public#926](https://github.com/sonatype/nexus-public/issues/926). Deploy a small PostgreSQL instance on the staging cluster.

*Pros:* Sonatype-supported chart, PostgreSQL-backed storage (no embedded DB corruption risk), clear upgrade path to Pro.
*Cons:* Requires maintaining a fork of a chart designed for Pro/HA. Adds a PostgreSQL dependency on the staging cluster. The patch may need updating as the chart evolves.

**Path 3: Use the stevehipwell/nexus3 community chart (recommended)**

[stevehipwell/nexus3](https://github.com/stevehipwell/helm-charts/tree/main/charts/nexus3) is the most widely used community chart for Nexus OSS on Kubernetes (165 stars, 90 forks, MIT license). It is actively maintained with roughly monthly releases tracking upstream Nexus versions. As of March 2026, the latest release is chart 5.19.0 / appVersion 3.89.0. Published to `ghcr.io/stevehipwell/helm-charts/nexus3` (OCI registry).

Key advantages over the Sonatype charts:

- **Designed for OSS**: No Pro license required, no patching needed
- **StatefulSet-based**: More appropriate for stateful workloads than the deprecated chart's Deployment
- **Config-as-code**: Declaratively configure proxy repositories, blob stores, cleanup policies, roles, users, and scheduled tasks via Helm values -- a Kubernetes Job calls the Nexus REST API post-install. This is particularly valuable for Scout's reproducible air-gapped deployments
- **Security defaults**: Read-only root filesystem, non-root user (UID 200), seccomp profiles
- **Custom CA certs**: Inject CA certificates into the JVM trust store via secret (useful for air-gapped TLS)
- **Prometheus metrics**: ServiceMonitor support for Grafana integration
- **Plugin installation**: Install Nexus plugins at startup via URL list

*Pros:* Purpose-built for OSS, actively maintained, feature-rich, declarative repository setup reduces manual post-deploy configuration.
*Cons:* Single maintainer (Steve Hipwell). Not an official Sonatype product. If the maintainer stops, you're in the same position as path 1 (working chart, no updates).

Other community charts exist but are not competitive: [Scalified/nexus](https://github.com/Scalified/helm-nexus) (1 star, automated version bumps only, no persistence or config features built in) and the abandoned oteemo/sonatype-nexus.

**Recommended deployment approach:**

Use the **stevehipwell/nexus3** chart (path 3). The config-as-code feature aligns well with Scout's infrastructure-as-code approach -- proxy repositories for conda, PyPI, Maven, and yum can be declared in Helm values and version-controlled alongside other Scout configuration. The single-maintainer risk is mitigated by the chart's maturity (5.x, years of releases) and the fact that the worst case is freezing on a working version (equivalent to path 1).

For a quick POC/evaluation, path 1 (image override on the deprecated chart) is acceptable.

#### ProGet (Inedo)

.NET-based universal package manager. Supports conda, PyPI, Maven, RPM, Docker, NuGet, npm, Helm, Debian, Alpine, and more. Pull-through proxying ("connectors") for all formats.

**Strengths:**
- Covers all five artifact types, like Nexus CE
- Free edition has **no feed/package/user limits** (unlike Nexus CE's 40K cap)
- Active development with regular releases (ProGet 2025.14 as of early 2026)

**Limitations:**
- **Proprietary software** (not open source) -- dependent on Inedo's continued goodwill for the free tier
- **No official Helm chart** -- Inedo says "several customers" run it on K8s, but you'd write your own chart
- .NET runtime on Linux/Docker -- unusual in the Linux/K8s ecosystem
- Free edition restrictions: no connector filters (paid only), no metadata caching (paid only), no security API (paid only), API deletes limited to 10/hour
- Less ecosystem presence and community support than Nexus/Artifactory

**Verdict:** The most interesting Nexus CE alternative if the 40K component cap becomes a real problem. The free edition restrictions are likely acceptable for Scout's use case. The trade-offs are proprietary license and no official K8s deployment path.

#### Pulp Project

Originally developed by Red Hat; powers Red Hat Satellite and Microsoft's Linux repositories. Plugin-based architecture with excellent RPM support (its original purpose).

**Supported formats:** RPM (very mature), containers, PyPI, Maven, Debian, Ansible, npm, Hugging Face models (via `pulp_hugging_face` plugin, v0.1.0), and more. **No conda plugin exists.**

**Strengths:**
- Best-in-class RPM repository management (on-demand sync, content versioning, rollback, import/export)
- Fully open source (GPLv2+), no usage caps, no commercial tiers
- Container pull-through caching could theoretically replace Harbor
- Kubernetes Operator available (`pulp-operator`)
- Active project with regular releases

**Limitations:**
- **No conda support** -- this is the critical gap
- Heavier to deploy than Nexus (needs PostgreSQL + Redis + worker processes)
- 1-3 GB RAM per worker (recommended 4+ workers = 4-12 GB RAM)

#### Artifactory (JFrog)

**OSS edition:** Only supports Maven/Gradle/Ivy. Useless for our multi-format needs.

**Pro edition ($6k+/year):** Supports 30+ formats including all five we need. No usage caps. Full pull-through proxy. The most feature-complete option -- if you're willing to pay.

### Single-Purpose Tool Assessments

#### devpi (PyPI proxy)

The most mature and feature-rich open-source PyPI proxy. Transparent pull-through caching: lazily fetches and caches packages on first request. Auto-updates its index via PyPI's changelog protocol.

**License:** MIT | **Resources:** ~560 MB RAM in production

**Strengths:**
- True pull-through proxy -- caches packages lazily on first request (exact model Scout needs)
- Private index support with inheritance (can layer private packages on top of PyPI cache)
- PostgreSQL backend available via `devpi-postgresql` plugin for production
- Very active: releases 6.17.0 (Aug 2025) through 6.19.1 (Feb 2026)

**Limitations:**
- Community Helm chart ([topiaruss/helm-devpi](https://github.com/topiaruss/helm-devpi)) described as early-stage
- Heavier than alternatives (runs a full index, not just a cache)
- PostgreSQL recommended for production (adds a dependency)

**Verdict:** Best-in-class PyPI proxy. If decoupling from Nexus, this is the clear choice for PyPI. However, if Nexus is deployed for conda, its PyPI proxy makes devpi redundant.

#### proxpi (PyPI proxy)

Lightweight CI-focused caching proxy. Caches index requests + package files to a local directory.

**License:** Apache 2.0 | **Resources:** Minimal (~32 MB RAM, 5 GB cache default)

**Limitations:** Designed for ephemeral CI use, not persistent services. No package upload support. Small community (183 GitHub stars). No Helm chart.

**Verdict:** Too lightweight for Scout's needs. devpi is the better choice.

#### Other PyPI tools (not proxies)

- **pypiserver**: Serves local packages from a directory but **does not cache upstream packages**. Has `--fallback-url` to redirect unfound packages to upstream PyPI, but the maintainer explicitly declined to add caching. Useful for hosting internal packages, not for proxying.
- **bandersnatch**: Official PyPA tool for mirroring PyPI. Full mirror is ~20+ TB. Supports selective mirroring by allowlist/blocklist, but you must predict packages in advance. Not a transparent proxy -- sync first, serve the directory via nginx.

#### Reposilite (Maven proxy)

Lightweight Maven repository manager. Proxies Maven Central and other repositories with local caching. Written in Kotlin, runs on JVM.

**License:** Apache 2.0 | **Resources:** ~20 MB RAM for personal use; scales up as needed

**Strengths:**
- Official Helm chart at [helm.reposilite.com](https://helm.reposilite.com/)
- Remarkably lightweight compared to Nexus (~20 MB vs ~4 GB RAM)
- Very active: 1.7k GitHub stars, latest release 3.5.26
- Supports proxied artifact storage (must be explicitly enabled)
- Configurable storage policies, allowed groups/extensions filtering

**Limitations:**
- Maven-only -- no PyPI, RPM, conda support
- Smaller community than Nexus/Artifactory

**Verdict:** Excellent if you need a standalone Maven proxy. Dramatically simpler and lighter than Nexus. However, if Nexus is already deployed for conda, adding Maven to Nexus costs nothing.

#### Apache Archiva (Maven proxy) -- RETIRED

Moved to Apache Attic in May 2024. No patches, no security updates. **Not a viable option.**

#### Quetz (Conda proxy)

The only open-source dedicated conda package server with proxy/pull-through support. Created by mamba-org. FastAPI-based Python app. Supports "proxy channels" that transparently cache packages on first request.

**License:** BSD-3-Clause | **Resources:** Not well-documented; needs PostgreSQL

**Strengths:**
- True pull-through proxy for conda channels (conda-forge, defaults, etc.)
- Fully open source, no usage caps

**Limitations:**
- **Maintenance is concerning**: Frontend declared "inactive" by Snyk. Core server has some activity but appears to be in maintenance mode, not active development.
- No Helm chart -- only Docker Compose documented; would require writing K8s manifests
- Limited community and documentation

**Verdict:** The only dedicated open-source conda pull-through proxy, but the maintenance trajectory is worrying. Would be risky to depend on this for a production air-gapped deployment.

#### Nginx Caching Proxy (generic HTTP cache for any format)

Nginx configured as a reverse proxy with `proxy_cache` in front of upstream package repositories. Documented configurations exist for [conda channels](https://gist.github.com/ei-grad/2d70c40c7956939f2564b988025ed665), [PyPI](https://gist.github.com/dctrwatson/5785638), [Maven](https://weblog.lkiesow.de/20170413-nginx-as-fast-maven-repository-proxy.html), and [RPM repos](https://www.getpagespeed.com/server-setup/hosting-rpm-repositories-with-nginx-and-cdn-with-blazing-speed).

**Strengths:**
- Works for any HTTP-based package repository
- Operationally simple -- nginx is already well-understood infrastructure
- No new software to learn or deploy
- Zero overhead beyond nginx itself

**Limitations:**
- **HTTPS handling**: Must terminate TLS to the upstream to cache responses. Clients must trust the proxy's cert or use HTTP.
- **No format awareness**: Doesn't understand package metadata. Cache invalidation is time-based only. Stale metadata (e.g., `repodata.json` for conda, `simple/` index for PyPI) can cause confusing errors.
- **Careful TTL tuning required**: Immutable content (package files) can be cached aggressively. Mutable content (index/metadata) needs short TTLs with periodic refresh.
- Each upstream needs its own `location` block and `proxy_pass` directive

**Verdict for conda specifically:** Conda channels are essentially static HTTP directories -- `repodata.json` (index) + `.tar.bz2`/`.conda` files (immutable packages). An nginx caching proxy is a viable low-tech fallback for conda if purpose-built tools are unavailable. Configure `channel_alias` or `custom_channels` in `.condarc` to point at the proxy.

**Verdict overall:** Inferior to purpose-built repository proxies for any format, but workable as a fallback. Most useful for conda where dedicated OSS alternatives are weakest.

#### Squid (forward caching proxy) -- Deep Dive

Squid is a general-purpose HTTP caching proxy that has been in production use for 30 years. It can cache any HTTP-based package repository (pip, conda, dnf, Maven) if properly configured. **Particularly relevant for Scout because Squid is also being considered as an authenticating forward proxy on the staging node** -- if already deployed, adding package caching is an incremental extension.

**License:** GPL v2 | **Resources:** ~1-2 GB RAM (with 1 GB `cache_mem`) + disk for cache

##### How Squid Caches Packages (SSL Bumping vs CONNECT Tunneling)

This is the critical distinction. Nearly all modern package repositories use HTTPS.

- **CONNECT tunneling (default for HTTPS)**: The client sends `CONNECT pypi.org:443` to Squid. Squid establishes a TCP tunnel -- an opaque, encrypted pipe. **Squid cannot see or cache anything inside the tunnel.** This is the default behavior. An HTTPS-only forward proxy with no SSL bump provides zero caching benefit.

- **SSL bumping (MITM decryption)**: Squid terminates the client's TLS connection using a dynamically-generated certificate signed by a local CA, then opens its own TLS connection to the upstream. Between those two connections, Squid sees plaintext HTTP and can cache responses. **This is the only way Squid can cache HTTPS content.**

- **Peek and splice (selective bumping)**: Squid 3.5+ can peek at the TLS Client Hello to read the SNI hostname, then decide per-domain whether to bump (MITM + cache) or splice (pass-through). This limits SSL bumping to known package repository domains:

```squid
acl step1 at_step SslBump1
acl package_repos ssl::server_name .pypi.org .pythonhosted.org
acl package_repos ssl::server_name .anaconda.org .conda.io .anaconda.com
acl package_repos ssl::server_name .maven.org .repo1.maven.org
acl package_repos ssl::server_name .rpm.rancher.io .download.nvidia.com .fedoraproject.org

ssl_bump peek step1
ssl_bump bump package_repos
ssl_bump splice all
```

##### CA Certificate Distribution

SSL bumping requires distributing a CA certificate to all clients:

- **Linux nodes** (for dnf): Copy to `/etc/pki/ca-trust/source/anchors/`, run `update-ca-trust`
- **Conda** (in Jupyter pods): `ssl_verify: /path/to/ca-bundle.crt` in `.condarc`
- **pip** (in Jupyter pods): `cert = /path/to/ca-bundle.crt` in `pip.conf`, or install into system trust store
- **Maven/Gradle**: Import into JVM keystore
- **K8s pods**: Mount CA cert as a volume, configure trust store in the container

This is operationally burdensome but manageable with Ansible. All Jupyter pods would need the CA cert mounted via the JupyterHub Helm chart's `extraFiles` or a ConfigMap.

##### Client-Side Proxy Configuration

Each package manager needs to know about the proxy:

| Package Manager | Configuration |
|----------------|---------------|
| **pip** | `proxy = http://squid:3128` in `/etc/pip.conf`, or `http_proxy`/`https_proxy` env vars |
| **conda** | `proxy_servers: {http: ..., https: ...}` in `.condarc`, or env vars (more reliable due to [conda proxy bugs](https://github.com/conda/conda/issues/5220)) |
| **dnf/yum** | `proxy=http://squid:3128` in `/etc/dnf/dnf.conf` or per-repo in `/etc/yum.repos.d/*.repo` |
| **Maven** | `<proxy>` section in `~/.m2/settings.xml`; Gradle uses `systemProp.http.proxyHost` |

Note: with a forward proxy, clients use **upstream URLs directly** (e.g., `https://pypi.org/simple`). This differs from Nexus/devpi where clients point to the proxy's own URL. Forward proxy configuration is simpler conceptually but means every client must be configured with proxy settings.

##### Cache Effectiveness by Content Type

| Content | Cacheable? | Notes |
|---------|-----------|-------|
| Package files (.whl, .tar.gz, .conda, .rpm, .jar) | Excellent | Immutable at a given URL. Cache aggressively with long TTLs. |
| PyPI `/simple/` index pages | Short TTL only | Change when new versions are published. 5-30 min TTL. |
| Conda `repodata.json` | Short TTL only | Large (100+ MB for conda-forge), changes frequently. Stale metadata causes confusing errors. |
| RPM `repomd.xml` | **Do not cache** | Points to other metadata files by checksum. Stale `repomd.xml` causes `dnf` to request files that no longer exist. |
| Maven `maven-metadata.xml` | Short TTL only | Changes when new versions are published. |

The metadata freshness problem is Squid's fundamental weakness vs format-aware proxies. Nexus/devpi understand when metadata has changed; Squid can only guess with TTLs.

##### Example squid.conf for Package Caching

```squid
# === SSL Bump (requires custom Squid image with --enable-ssl-crtd) ===
http_port 3128 ssl-bump generate-host-certificates=on \
  cert=/etc/squid/ssl/squid-ca.crt key=/etc/squid/ssl/squid-ca.key
sslcrtd_program /usr/lib/squid/security_file_certgen -s /var/lib/squid/ssl_db -M 16MB

# === Selective SSL Bump ===
acl step1 at_step SslBump1
acl package_repos ssl::server_name .pypi.org .pythonhosted.org
acl package_repos ssl::server_name .anaconda.org .conda.io .anaconda.com
acl package_repos ssl::server_name .maven.org .repo1.maven.org
acl package_repos ssl::server_name .rpm.rancher.io .download.nvidia.com .fedoraproject.org
ssl_bump peek step1
ssl_bump bump package_repos
ssl_bump splice all

# === Cache Storage ===
cache_dir aufs /var/spool/squid 50000 16 256    # 50 GB on disk
maximum_object_size 2 GB                          # PyTorch wheels are 700 MB-2 GB
cache_mem 1024 MB
maximum_object_size_in_memory 128 MB
cache_replacement_policy heap LFUDA               # Keeps popular large objects

# === Refresh Patterns ===
# Immutable package files: cache 90 days
refresh_pattern -i \.(rpm|drpm|whl|tar\.gz|tar\.bz2|conda|jar|pom)$ \
  129600 100% 129600 override-expire override-lastmod reload-into-ims ignore-reload

# RPM repomd.xml: NEVER cache (stale repomd causes broken dnf)
acl repomd url_regex /repomd\.xml$
cache deny repomd

# Conda repodata, PyPI index, Maven metadata: short TTL (5-30 min)
refresh_pattern -i repodata\.json$       5 20% 30 reload-into-ims
refresh_pattern -i /simple/              5 20% 30 reload-into-ims
refresh_pattern -i maven-metadata\.xml$  5 20% 30 reload-into-ims

# Default
refresh_pattern .  0 20% 4320
```

**Important gotcha**: The standard Squid Docker images (e.g., `ubuntu/squid`) do **not** include SSL bump support. You need a custom image built with `--enable-ssl-crtd`, or use pre-built options like `satishweb/squid-ssl-proxy` or `salrashid123/squidproxy`.

##### K8s Deployment

Several community Helm charts exist ([lifen/squid](https://artifacthub.io/packages/helm/lifen/squid), [holosix/squid-helm](https://github.com/holosix/squid-helm)) but none are mature or support SSL bumping out of the box. A custom chart or Ansible-managed deployment is more realistic. The core deployment is straightforward: StatefulSet + ConfigMap (squid.conf) + PVC (cache dir) + Secret (CA cert/key).

##### If Squid Is Already Deployed as a Forward/Auth Proxy

**If SSL bump is NOT yet enabled**: Adding caching requires a significant reconfiguration -- new image with SSL support, CA generation and distribution, `ssl_bump` directives, `sslcrtd_program` setup. This is not a trivial add-on.

**If SSL bump IS already enabled** (e.g., for traffic inspection): Adding package caching is mostly additive -- add `cache_dir`, `maximum_object_size`, `refresh_pattern` rules, and `cache deny` rules for volatile metadata. This is a natural extension of the same Squid instance.

The same Squid instance can serve both roles (auth proxy + package cache). Use ACLs to control what gets cached.

##### Squid vs Purpose-Built Proxies

| Dimension | Squid | Nexus CE / devpi / Reposilite |
|-----------|-------|-------------------------------|
| Formats | Any HTTP (universal) | Format-specific |
| Caching intelligence | URL-based, TTL guessing | Format-aware, metadata-driven |
| HTTPS caching | Requires SSL bump (MITM) + CA distribution | Native (clients point to proxy URL) |
| Metadata freshness | Stale metadata causes real problems | Intelligent sync |
| Resource usage | ~1-2 GB RAM | ~4-5 GB (Nexus) / ~0.5 GB (devpi) |
| Usage caps | None | 40K components (Nexus CE) |
| UI/Management | Access logs only | Web UI, REST API |
| Vendor risk | GPL, community, stable | Nexus CE trending restrictive |

**Verdict:** Squid is a viable package caching solution if SSL bumping is already in place for other reasons. The main weakness is metadata freshness -- stale `repomd.xml` and `repodata.json` cause operational pain that format-aware proxies handle automatically. Best used as: (1) a supplement alongside format-aware proxies, or (2) a "good enough" single solution if the operational overhead of Nexus is not justified and careful TTL tuning is acceptable.

#### Varnish

High-performance HTTP accelerator. The open-source version does not support HTTPS natively (needs a TLS terminator in front). VCL configuration language is powerful but has a learning curve. Varnish Enterprise supports TLS but is commercial. Less applicable than Squid for this use case because Squid's forward-proxy mode is a more natural fit for package managers (which expect to configure a proxy, not rewrite URLs to point at a reverse proxy).

### Emerging Developments (2024-2026)

**Conda OCI distribution:** conda-forge is being mirrored to GHCR as OCI artifacts via `conda-oci-mirror`. A Conda Enhancement Proposal is in progress to standardize OCI-based distribution. If this matures, Harbor (which Scout already has) could serve as a conda package cache. Still experimental -- needs `conda-oci-forwarder` compatibility layer on the client side.

**Nexus CE trajectory:** Sonatype launched "Nexus One" (cloud-first AI-native platform) in Nov 2025. Community Edition received new format support (Hugging Face, Cargo, Conan v2) but component limits were reduced from 100K to 40K. The strategic direction is clearly toward commercial cloud services. CE continues to exist but may become more restrictive over time.

**Quetz stagnation:** The mamba-org conda server project has lost momentum. This was the only dedicated open-source conda proxy, and it appears to be fading.

**No significant new entrants:** Despite the pain created by Nexus CE limits and Artifactory pricing, no significant new open-source multi-format repository proxy has emerged in 2024-2026. The space remains dominated by Nexus, Artifactory, and Pulp.

**OCI registries for ML models:** Harbor 2.13+ has dedicated support for ML models as OCI artifacts (CNAI metadata annotations, replication from HuggingFace, P2P preheat). The CNCF [KitOps/ModelKit](https://kitops.org/) project and emerging [ModelPack specification](https://github.com/oras-project/modelpack) (backed by PayPal, ByteDance, Red Hat) are standardizing ML model packaging as OCI artifacts. If this trend matures, Harbor could serve as a unified cache for both container images and ML models.

---

## ML Model Proxying

Scout's optional Chat feature (Open WebUI + Ollama) and the Jupyter embedding notebook already involve large ML model files. As AI/ML workloads grow, model distribution in air-gapped environments becomes another proxying need.

### Current State

- **Ollama models**: Distributed via NFS shared storage (ADR 0008). Models are pre-staged on staging cluster, mounted read-only on production. This works well.
- **HuggingFace models**: Currently baked into Docker images or downloaded at runtime. No caching infrastructure.

### Tool Landscape for Model Proxying

#### Nexus CE HuggingFace Proxy

Nexus CE added HuggingFace proxy support in version 3.77.0. Creates a `huggingface (proxy)` repository with remote URL `https://huggingface.co`. Requires PostgreSQL backend (not H2).

**Strengths:**
- If Nexus is already deployed for package proxying, adding a HuggingFace proxy repository is zero additional tools
- Supports Bearer Token authentication for gated/private models
- Available in CE (free), not Pro-only

**Significant limitations:**
- **Proxy only** -- no hosted or group repositories. Can cache upstream models but cannot host private models.
- **Models only** -- datasets and Spaces are not supported.
- **Cache bloat** -- different resolve operations for the same model re-download and cache identical files (no deduplication). [Open issue #698](https://github.com/sonatype/nexus-public/issues/698).
- **Xet storage migration risk** -- HuggingFace is migrating from Git LFS to Xet storage (chunk-level deduplication). Xet became the default for new users in 2025. Reports of proxy failures for Xet-backed files ([issue #648](https://github.com/sonatype/nexus-public/issues/648)). **As more models migrate to Xet, this proxy may break for newer content.**
- NFS/EFS storage recommended; S3 blob stores have performance problems with large model files.

**Verdict:** Functional today for Git LFS-backed models, but the Xet migration trajectory is a significant risk. Worth using opportunistically if Nexus is already deployed, but not worth deploying Nexus *for*.

#### Olah -- Lightweight HuggingFace Mirror

[Olah](https://github.com/vtuber-plan/olah) is a self-hosted HuggingFace mirror. `pip install olah`, then `olah-cli` to start. MIT licensed.

**Strengths:**
- **Offline mode** -- serve cached content only, perfect for air-gapped production. Cache on staging with internet, serve offline on production.
- **Chunk-level caching** -- partial downloads are cached, unlike Nexus which re-downloads full files per resolve
- Supports models, datasets, and Spaces
- LRU/FIFO/LARGE_FIRST eviction strategies, repository whitelist/blacklist
- Lightweight -- single Python process
- Clients configure `HF_ENDPOINT=http://olah:8090` and use standard `huggingface-cli` / `huggingface_hub` library

**Limitations:**
- Cache cannot migrate between Olah versions (must delete cache on upgrade)
- Smaller community than Nexus
- No Helm chart (but trivial to deploy as a single-container pod)

**Verdict:** The most practical dedicated tool for air-gapped HuggingFace model distribution. The offline mode directly mirrors Scout's existing Ollama NFS pattern (ADR 0008).

#### Shared NFS + `HF_HOME` (simplest approach)

The `huggingface_hub` library supports redirecting its cache to a shared directory:

```bash
export HF_HOME=/shared/nfs/huggingface
export HF_HUB_OFFLINE=1  # on air-gapped production nodes
```

Download models on staging (internet-connected), then mount the same NFS path read-only on production. The library uses file locking for concurrent access, which works on NFS (unlike SQLite).

**Verdict:** Simplest possible approach. Directly analogous to the Ollama NFS pattern in ADR 0008. No proxy service needed -- just shared filesystem. Best for a small number of known models.

#### Pulp HuggingFace Plugin

[`pulp_hugging_face`](https://github.com/pulp/pulp_hugging_face) -- version 0.1.0 (August 2025). Pull-through caching for models, datasets, and Spaces. Supports Git LFS. GPL v2.0.

**Verdict:** More comprehensive API than Nexus, but very early stage (73 commits, 1 star) and requires the full Pulp deployment stack. Overkill unless Pulp is already deployed for RPM proxying.

#### Generic HTTP Proxies (Squid/nginx) for HuggingFace

**Not effective.** HuggingFace model downloads use Git LFS (and increasingly Xet), which involves API calls that return signed, time-limited CDN URLs. The actual download URLs change per request, defeating cache key matching. A generic proxy would cache the redirect responses but not the actual model content in a useful way.

### Recommendation for Model Proxying

**Short-term:** Continue the NFS shared storage pattern from ADR 0008. For HuggingFace models, use `HF_HOME` on shared NFS + `HF_HUB_OFFLINE=1` on production. This is consistent, simple, and proven.

**If model variety/frequency grows:** Deploy Olah on staging. Its offline mode, chunk-level caching, and lightweight footprint make it the best fit for Scout's air-gapped architecture. Clients need only `HF_ENDPOINT` set.

**If Nexus is already deployed:** Add a HuggingFace proxy repository opportunistically, but monitor the Xet migration. Do not depend on it as the sole HuggingFace caching mechanism.

**Longer-term:** Watch the OCI-based model distribution trend. Harbor 2.13+ can store ML models as OCI artifacts, and tools like ORAS and KitOps are standardizing this. If this matures, Harbor could serve as a unified cache for container images, conda packages (via conda-oci-mirror), and ML models -- consolidating three concerns into one existing tool.

---

## Architecture Options

The key question: **one proxy or many?**

### Option A: Nexus CE as unified proxy (replacing Harbor)

```
Internet ← Nexus CE (staging) ← Production K3s
             - conda channels
             - PyPI
             - Maven Central
             - RPM repos (Rancher, NVIDIA, EPEL)
             - Container images (Docker Hub, GHCR, Quay)
```

**Pros:** Single tool. Simplest to operate. Covers everything.

**Cons:** The 40K component limit is a real risk when proxying containers (each layer = component) + RPM repos + conda + PyPI + Maven. You already have Harbor working; replacing it is unnecessary churn. Harbor has superior container-specific features (vulnerability scanning, content trust, replication). Single point of failure for *all* artifact types.

**Verdict: Not recommended.** The component cap makes this fragile for the combined workload, and throwing away a working Harbor setup is wasteful.

### Option B: Harbor (containers) + Nexus CE (everything else)

```
Internet ← Harbor (staging) ← Production K3s (container images)
Internet ← Nexus CE (staging) ← Production K3s (conda, PyPI, Maven, RPM)
```

**Pros:** Harbor continues doing what it does well. Nexus handles conda (which only it and Artifactory Pro support) plus PyPI, Maven, and RPM. Keeping Docker images out of Nexus preserves component cap headroom for packages. Two tools instead of three or four.

**Cons:** The 40K component limit is still a concern for packages alone if usage grows. Two tools to maintain (but one already exists). Nexus is 4-5 GB RAM on the staging node.

**Verdict: Recommended starting point.** This covers all formats with the fewest new tools. Monitor the component cap; if it becomes a problem, consider upgrading to Nexus Pro or splitting RPM to Pulp.

### Option C: Harbor (containers) + Nexus CE (conda, PyPI, Maven) + Pulp (RPM)

```
Internet ← Harbor (staging) ← Production K3s (container images)
Internet ← Nexus CE (staging) ← Production K3s (conda, PyPI, Maven)
Internet ← Pulp (staging) ← Production K3s (RPM/yum)
```

**Pros:** Best-of-breed for each format. Pulp is unmatched for RPM (content versioning, rollback, fine-grained sync policies). Reduces Nexus component count pressure. No usage caps on either Pulp or Harbor.

**Cons:** Three tools to maintain. Pulp requires PostgreSQL + Redis + workers (4-12 GB RAM). Significant operational overhead for only three RPM use cases today.

**Verdict: Overkill unless RPM needs grow substantially.** If Scout eventually needs to proxy full RHEL/CentOS base repos for node provisioning, Pulp becomes more attractive. For three packages, Nexus's yum proxy is sufficient.

### Option D: Single-purpose tools (no Nexus)

```
Internet ← Harbor (staging) ← Production K3s (container images)
Internet ← nginx cache (staging) ← Production K3s (conda channels)
Internet ← devpi (staging) ← Production K3s (PyPI)
Internet ← Reposilite (staging) ← Production K3s (Maven JARs)
Internet ← nginx cache or Nexus CE (staging) ← Production K3s (RPM)
```

**Pros:** Each tool is best-of-breed (or at least purpose-built) for its format. devpi and Reposilite are well-maintained, truly open source, and lightweight. No single-tool usage caps to worry about. Total RAM for devpi (~560 MB) + Reposilite (~20 MB) + nginx (~50 MB) ≈ 630 MB, vs Nexus CE at ~4-5 GB.

**Cons:** Three or four tools to deploy and maintain instead of one. The **conda story is the weakest link** -- no well-maintained dedicated conda proxy exists, so you're left with an nginx caching proxy (workable but lacks metadata awareness) or Quetz (maintenance concerns). Operational complexity of managing multiple services. RPM proxying via nginx cache is less clean than a purpose-built yum proxy (would need careful TTL tuning for repo metadata).

**Verdict: Viable if Nexus's OSS trajectory is unacceptable**, but the conda gap is the main concern. If conda OCI distribution matures (allowing Harbor to serve as the conda cache), this option becomes much more attractive.

### Option E: ProGet free edition (replacing Nexus CE)

```
Internet ← Harbor (staging) ← Production K3s (container images)
Internet ← ProGet (staging) ← Production K3s (conda, PyPI, Maven, RPM)
```

**Pros:** Same architecture as Option B but with no usage caps. ProGet's free edition has no feed, package, or user limits. Covers all formats including conda. Active development.

**Cons:** **Proprietary software** -- not open source. Dependent on Inedo's continued free-tier policy. No official Helm chart (must write your own). .NET runtime is unusual in Linux/K8s environments. Free edition lacks connector filters, metadata caching, and security API (paid features). Less community support and ecosystem presence than Nexus.

**Verdict: Worth evaluating if Nexus CE's 40K cap becomes a real problem.** The proprietary license is the main concern for a project that values open-source tooling. A good "Plan B" to keep in the back pocket.

### Option F: Harbor (containers) + Squid (everything else)

```
Internet ← Harbor (staging) ← Production K3s (container images)
Internet ← Squid with SSL bump (staging) ← Production K3s (conda, PyPI, Maven, RPM)
```

**Pros:** If Squid is already being deployed on staging as a forward/auth proxy, adding package caching is incremental configuration (not a new tool). No usage caps. Universal -- works for any HTTP-based package manager without per-format repository setup. Lowest resource footprint (~1-2 GB RAM). No JVM, no database dependencies. GPL, community-maintained, no vendor risk.

**Cons:** Requires SSL bumping (MITM decryption) to cache HTTPS content, which means CA cert distribution to all clients (production nodes, Jupyter pods). **Metadata freshness is the Achilles' heel** -- stale `repomd.xml` breaks dnf, stale `repodata.json` confuses conda. Requires careful per-format TTL tuning. No web UI, no package browsing, no cleanup policies beyond LRU eviction. No HuggingFace model caching (Git LFS/Xet signed URLs defeat URL-based caching). Standard Squid Docker images lack SSL bump support -- need a custom image.

**Verdict: Compelling if Squid is already deployed with SSL bump for auth/inspection.** The marginal cost of adding package caching to an existing Squid instance is low. As a purpose-built package proxy replacement (deploying Squid *only* for caching), it's harder to justify vs Nexus CE because you accept metadata freshness problems and CA distribution overhead without the benefit of a shared auth proxy. Best paired with the understanding that metadata TTL tuning will need iteration and that some operational friction (stale metadata, no UI) is the trade-off for simplicity and zero vendor risk.

### Option G: Artifactory Pro (everything)

```
Internet ← Artifactory Pro (staging) ← Production K3s (everything)
```

**Pros:** No usage caps, no limitations, every format, one tool, commercial support.

**Cons:** $6,000+/year. Replaces working Harbor setup unnecessarily.

**Verdict: Only if budget allows and simplicity is paramount.**

### Recommendation

**If Squid is already being deployed on staging** (e.g., as a forward/auth proxy with SSL bump), **start with Option F (Harbor + Squid)**. The marginal cost of adding package caching to an existing Squid instance is very low -- it's additive configuration, not a new tool. Accept the metadata freshness trade-offs (careful TTL tuning for `repodata.json`, `repomd.xml`, PyPI index) and iterate. If the TTL-based approach causes too much operational friction, add Nexus CE or individual format-aware proxies alongside Squid.

**If Squid is NOT already deployed**, **start with Option B (Harbor + Nexus CE)**. Deploying Squid solely for package caching (with the SSL bump + CA distribution overhead) is harder to justify vs Nexus, which handles HTTPS natively and understands package metadata.

**Why conda constrains the choice:** No well-maintained, truly open-source conda pull-through proxy exists -- Quetz (mamba-org) is stagnating, and the only format-aware alternatives are Nexus CE, Artifactory Pro ($6k+/yr), or ProGet (proprietary). Squid and nginx can cache conda channels at the HTTP level (workable but not metadata-aware). Until conda OCI distribution matures (which would let Harbor serve as a conda cache), there is no clean single-purpose solution for conda.

**Escalation path:**
1. If Squid TTL issues cause operational pain: add Nexus CE for conda + PyPI (the most metadata-sensitive formats), keep Squid for RPM + Maven (simpler metadata)
2. If Nexus CE's 40K cap is hit: clean up unused components (Nexus has cleanup policies), then split RPM to Pulp (Option C)
3. If Sonatype further restricts CE: evaluate ProGet free edition (Option E) as a drop-in replacement -- same multi-format coverage, no usage caps, but proprietary
4. If budget allows: Artifactory Pro (Option G) removes all constraints
5. Long-term: watch conda OCI distribution -- if it matures, Option D (single-purpose tools) becomes viable and eliminates the Nexus dependency entirely

---

## Impact on the Jupyter Image

With a conda/PyPI proxy in place, the image can be dramatically simplified.

### What stays in the image (must be baked in)

- **Base Jupyter environment**: The `jupyter/pyspark-notebook` base image (provides JVM, PySpark, conda, JupyterLab)
- **Spark JARs**: JVM dependencies loaded at Spark startup. However, Nexus proxies Maven Central, so a startup script could fetch them -- see "Launch-Time Customization" below.
- **nb_conda_kernels**: Enables user-created conda envs to appear as Jupyter kernels. Core infrastructure.
- **Core JupyterLab extensions**: `jupyter-collaboration`, `jupyter-resource-usage`, `jupyterlab-git`

### What moves out of the image (users install at runtime)

- **Data science packages**: torch, transformers, scikit-learn, seaborn, pandas, numpy, matplotlib, plotly, etc.
- **Scout-specific packages**: trino, delta-spark, s3fs, boto3, pyarrow
- **Embedding packages**: deepspeed, FlagEmbedding, sentence-transformers, faiss-gpu, etc.

### What this enables

- **One base image** instead of two (or more)
- **Smaller image**: Removing torch alone saves ~2 GB; the full data science stack is likely 5-8 GB
- **Faster builds and pulls**
- **User-controlled versions**: Users create conda environments with the versions they need
- **CVE flexibility**: Out-of-date packages are the user's choice, not ours to maintain

### Transition path for existing users

Existing notebooks import packages that are currently pre-installed. To avoid breaking them:

1. Ship a **default conda environment spec** (an `environment.yml`) as a sample alongside the Quickstart notebook
2. The postStart hook (or init container) can create this environment on first login if it doesn't exist
3. Users who want different versions create their own environments
4. Document the change clearly; provide migration instructions

---

## Impact on RPM Installation

With an RPM proxy on Nexus, production nodes can use standard dnf repository configuration. The entire `download_rpms_via_job.yaml` / `install_rpms_from_artifacts.yaml` framework can be retired.

### Before (current)

```
Ansible playbook
  → ensure_packages.yaml (checks if installed)
  → download_rpms_via_job.yaml
    → Creates K8s Job on staging cluster (Rocky Linux 9 container)
    → Init container: dnf download --resolve from upstream repos
    → kubectl cp: pod → staging node
    → fetch: staging node → Ansible control node
  → install_rpms_from_artifacts.yaml
    → copy: control node → production node
    → Extract tar.gz
    → Create temporary /etc/yum.repos.d/local-*.repo
    → dnf install from local repo
    → Clean up temp repo
```

### After (with Nexus yum proxy)

```
Ansible playbook
  → dnf install <package>
    (dnf.conf points to Nexus proxy repos on staging)
```

The production nodes would have `/etc/yum.repos.d/` configurations pointing to Nexus yum proxy repositories on the staging node. Nexus caches RPMs on first request. Subsequent installs are served from cache.

### Configuration

Each upstream repo needs a Nexus yum proxy repository:

| Nexus Proxy Repo | Upstream | Used For |
|-----------------|----------|----------|
| `rancher-k3s` | `https://rpm.rancher.io/k3s/stable/common/centos/9/noarch` | k3s-selinux |
| `nvidia-container` | NVIDIA container toolkit repo | nvidia-container-toolkit |
| `epel` | EPEL 9 | python3-kubernetes |

Production node repo files (managed by Ansible):

```ini
[nexus-rancher-k3s]
name=Rancher K3s (via Nexus)
baseurl=http://nexus.staging:8081/repository/rancher-k3s/
enabled=1
gpgcheck=0

[nexus-epel]
name=EPEL 9 (via Nexus)
baseurl=http://nexus.staging:8081/repository/epel/
enabled=1
gpgcheck=0
```

This is dramatically simpler and aligns with how package management is supposed to work.

---

## Transparent Client Configuration (Jupyter)

Configuration is injected into every notebook pod so users don't need to know about the proxy.

**For conda** -- mount `.condarc` via the JupyterHub Helm chart's `extraFiles`:

```yaml
singleuser:
  extraFiles:
    condarc:
      mountPath: /opt/conda/.condarc
      stringData: |
        channels:
          - http://nexus.staging:8081/repository/conda-forge/
          - http://nexus.staging:8081/repository/conda-defaults/
        default_channels:
          - http://nexus.staging:8081/repository/conda-defaults/
        channel_alias: http://nexus.staging:8081/repository/
        envs_dirs:
          - /home/jovyan/envs
          - /opt/conda/envs
```

The `channel_alias` setting rewrites channel name references. When a user runs `conda install -c conda-forge numpy`, it resolves to the proxy URL automatically.

**For pip** -- mount `pip.conf`:

```yaml
singleuser:
  extraFiles:
    pip-conf:
      mountPath: /etc/pip.conf
      stringData: |
        [global]
        index-url = http://nexus.staging:8081/repository/pypi-proxy/simple
        trusted-host = nexus.staging
```

**Network policy**: No changes are needed. The default JupyterHub network policy already includes an `ipBlock` egress rule that allows traffic to `0.0.0.0/0` with exceptions for RFC 1918 private ranges (`10.0.0.0/8`, `172.16.0.0/12`, `192.168.0.0/16`). Since the staging node is reachable via its Tailscale IP (in the `100.64.0.0/10` CGNAT range), it falls outside the denied ranges and is already permitted. DNS resolution also works -- the default policy allows DNS (port 53) to `kube-system` and private ranges. This means proxy URLs using Tailnet hostnames (e.g., `http://nexus.staging.your-tailnet.ts.net:8081/`) will resolve and connect without any network policy modifications.

---

## The Spark Question

Spark is the most complex dependency and the hardest to move out of the image.

### Option A: Keep Spark in the image (recommended short-term)

Continue using `pyspark-notebook` as the base image. This provides JVM, PySpark, and Spark binaries.

The custom JARs (Delta, Hadoop-AWS, smolder) still need to be present. Options:
1. **Keep them in the Dockerfile** (status quo) -- simplest, but still hard-coded
2. **Fetch via init container or postStart hook from the Maven proxy** -- more flexible, but adds startup latency

### Option B: Remove Spark from the base image (longer-term)

If users primarily need SQL access to the Delta Lake, **Trino via the `trino` Python package** is a lighter alternative:

```python
from trino.dbapi import connect
conn = connect(host="trino", port=8080, catalog="delta", schema="default")
cursor = conn.cursor()
cursor.execute("SELECT * FROM reports WHERE modality = 'CT' LIMIT 100")
df = pd.DataFrame(cursor.fetchall())
```

This eliminates PySpark, JVM, and all Spark JARs. The base image could be `jupyter/scipy-notebook` instead of `jupyter/pyspark-notebook` -- significantly smaller.

**Tradeoffs:** Breaks existing PySpark notebooks. Loses Spark-specific functionality (UDFs, MLlib). Trino is read-only against the Delta Lake.

**A middle path**: Offer Spark as an optional profile. Users who need it select a Spark profile; users who only need SQL get the lightweight Trino-only profile.

### Option C: Spark JARs via Maven proxy

Nexus can proxy Maven Central. Instead of hard-coding JAR URLs in the Dockerfile, a startup script fetches them:

```bash
#!/bin/bash
MAVEN_PROXY="http://nexus.staging:8081/repository/maven-central"
JARS=(
  "io/delta/delta-spark_2.12/3.3.0/delta-spark_2.12-3.3.0.jar"
  "io/delta/delta-storage/3.3.0/delta-storage-3.3.0.jar"
  # ... etc
)
for jar in "${JARS[@]}"; do
  curl -sO "${MAVEN_PROXY}/${jar}" -o "${SPARK_HOME}/jars/$(basename $jar)"
done
```

This makes JAR versions configurable via ConfigMap without rebuilding the image. Adds ~30-60s to pod startup (cached after first pull).

---

## Launch-Time Customization

JupyterHub provides several mechanisms for running setup scripts when a notebook pod starts.

### Mechanisms

| Mechanism | Runs Before Notebook? | Per-User? | User Self-Service? |
|-----------|----------------------|-----------|-------------------|
| `lifecycleHooks.postStart` | No (async) | No | No |
| `initContainers` | Yes (guaranteed) | Limited | No |
| `profileList` | N/A (sets config) | Menu choice | Choose from list |
| `pre_spawn_hook` (Python) | Hub-side only | Yes | No |
| Mounted scripts via ConfigMap/PVC | Depends | Yes | Possible |

### Recommended Approach: Layered Customization

**Layer 1: Admin-controlled init container** (runs before notebook starts)

An init container that fetches Spark JARs from the Maven proxy (if externalizing JARs), sets up proxy configuration, and runs admin-defined setup scripts from a ConfigMap.

```yaml
singleuser:
  initContainers:
    - name: setup
      image: curlimages/curl
      command: ["/bin/sh", "/scripts/init.sh"]
      volumeMounts:
        - name: spark-jars
          mountPath: /spark-jars
        - name: init-scripts
          mountPath: /scripts
```

**Layer 2: User-controlled startup script** (postStart hook)

Check for a user-provided script in the persistent home directory:

```yaml
singleuser:
  lifecycleHooks:
    postStart:
      exec:
        command:
          - /bin/sh
          - -c
          - |
            if [ ! -f /home/jovyan/.scout_quickstart ]; then
              cp -rn /opt/scout/samples/* /home/jovyan/Scout/ 2>/dev/null
              touch /home/jovyan/.scout_quickstart
            fi
            if [ -f /home/jovyan/.scout/startup.sh ]; then
              /bin/sh /home/jovyan/.scout/startup.sh
            fi
```

Users create `~/.scout/startup.sh` in their persistent home directory to automate environment setup:

```bash
#!/bin/bash
conda activate my-env || conda env create -f ~/my-env.yml
```

**Caveats:** `postStart` runs asynchronously -- the notebook UI may appear before the script finishes. For scripts that must complete first, an init container is better but doesn't have straightforward access to the user's PVC.

**Layer 3: Profile-based environment selection**

Scout already uses `profileList` for CPU/memory sizing. This extends it for environment selection:

```yaml
singleuser:
  profileList:
    - display_name: "Standard (Trino SQL + PySpark)"
      description: "General data analysis"
      default: true
    - display_name: "ML/Embedding (GPU)"
      description: "GPU-accelerated ML workloads"
      kubespawner_override:
        image: "ghcr.io/washu-tag/pyspark-notebook:gpu"
        extra_resource_limits:
          nvidia.com/gpu: "1"
```

### Domino-Style Comparison

Domino Data Lab's model: admin-curated image → user Dockerfile instructions → pre-run scripts → project `requirements.txt` → per-user env vars.

Scout's equivalent: admin-curated slim image → conda environments via proxy (replaces Dockerfile customization) → `~/.scout/startup.sh` → `environment.yml` in project directory → per-user env vars via `pre_spawn_hook`.

The key difference: Domino rebuilds images; Scout avoids rebuilds entirely by using runtime package installation through the proxy.

---

## Implementation Plan

### Phase 1: Deploy Nexus on staging

1. Deploy Nexus CE using the [stevehipwell/nexus3](https://github.com/stevehipwell/helm-charts/tree/main/charts/nexus3) Helm chart (`ghcr.io/stevehipwell/helm-charts/nexus3`) with Traefik ingress on the staging node
2. Configure proxy repositories declaratively via the chart's config-as-code values:
   - Conda: `conda-forge`, `conda-defaults`
   - PyPI: `pypi-proxy` → `https://pypi.org/`
   - Maven: `maven-central` → `https://repo1.maven.org/maven2/`
   - Yum: `rancher-k3s`, `nvidia-container`, `epel`
3. Test connectivity from production cluster to staging proxy
4. Add Ansible role for Nexus deployment (alongside existing Harbor staging role)
5. Monitor component usage against the 40K cap

### Phase 2: RPM proxy (quick win)

This is the easiest to validate and has immediate payoff:

1. Configure Nexus yum proxy repositories for Rancher, NVIDIA, and EPEL
2. Create Ansible-managed `/etc/yum.repos.d/` files on production nodes pointing to Nexus
3. Modify `ensure_packages.yaml` to use direct `dnf install` when proxy is available
4. Test K3s SELinux, NVIDIA, and python3-kubernetes package installation through proxy
5. Once validated, deprecate the `download_rpms_via_job.yaml` / `install_rpms_from_artifacts.yaml` path

### Phase 3: Configure Jupyter to use conda/PyPI proxy

1. Add `.condarc` and `pip.conf` to Helm values via `extraFiles`
2. Add network policy egress rule for proxy access
3. Install `nb_conda_kernels` in the standard image (already in embedding image)
4. Configure `envs_dirs` for persistent user environments
5. Update the Quickstart notebook to demonstrate creating a conda environment
6. Ship a default `environment.yml` with the standard data science packages

### Phase 4: Slim down the Jupyter image

1. Remove pre-installed data science packages from requirements.txt
2. Keep only JupyterLab core, `nb_conda_kernels`, and infrastructure packages
3. Merge the two Dockerfiles into one base image
4. Test that the default `environment.yml` recreates the expected environment via proxy

### Phase 5 (optional): Externalize Spark JARs

1. Configure Nexus Maven proxy (done in Phase 1)
2. Create init container or startup script to fetch JARs from Maven proxy
3. Make JAR versions configurable via ConfigMap
4. Remove hardcoded JAR URLs from Dockerfile

### Phase 6 (optional): Trino-only lightweight profile

1. Create a `scipy-notebook`-based image without Spark
2. Add as a profile option in `profileList`
3. Document the Trino-only workflow as an alternative to PySpark

---

## Open Questions

1. **Nexus component cap and OSS trajectory**: The 40K component limit is the biggest risk. With pull-through proxying (only caching what's requested), we may stay under this for a long time, but it needs monitoring. Sonatype's strategic direction (cloud-first "Nexus One", reduced CE limits from 100K to 40K) suggests CE may become more restrictive over time. If it becomes a problem, options are: cleanup policies, splitting RPM to Pulp, switching to ProGet free edition, or upgrading to Nexus Pro.

2. **Proxy availability**: If the staging node or Nexus goes down, no new packages can be installed (existing cached packages are still served). Nexus stores its cache on disk (should use a persistent volume), and the staging node is already a single point of failure for Harbor. This doesn't change the risk profile much.

3. **First-run experience**: If users need to create a conda environment before they can do anything, the first notebook experience degrades. The postStart script could auto-create a default environment, but `conda env create` for a full data science stack takes several minutes. Consider pre-warming the proxy cache during deployment.

4. **Spark version coupling**: Spark, Delta Lake, Hadoop, and the AWS SDK have tight version interdependencies. Making these user-configurable risks subtle breakage. Spark JARs should probably remain admin-controlled even if Python packages become user-controlled.

5. **Storage for user environments**: Conda environments can be large (several GB for ML stacks). The current default PVC may need to increase, or we need to document environment cleanup procedures.

6. **Security posture**: With a proxy, users can install arbitrary packages (from the proxy cache). The air gap still prevents reaching the internet directly, but the proxy cache grows organically. Is this acceptable to the security team? Nexus has features for blocking specific packages if needed.

7. **Should Nexus replace Harbor?** Probably not. Harbor is purpose-built for containers (vulnerability scanning, content trust, CNCF graduated project) and is already deployed. The component cap concern also argues against consolidating Docker images into Nexus. But it's worth noting that Nexus *can* do it if simplification is ever prioritized over features.

8. **Phase 2 scope**: The RPM proxy work could be done independently of the Jupyter work. It's a simpler, lower-risk change that immediately simplifies the Ansible codebase. Consider prioritizing it.

9. **Conda OCI distribution**: conda-forge is being mirrored to GHCR as OCI artifacts. If conda OCI distribution matures (standardized via Conda Enhancement Proposal + `conda-oci-forwarder` client compatibility), Harbor could serve as the conda cache. This would eliminate the main reason Nexus is hard to replace. Worth monitoring but too experimental to depend on today.

10. **ProGet as a backup plan**: ProGet's free edition covers all formats with no usage caps. The main trade-off is proprietary license. Worth doing a quick evaluation to validate it works for Scout's specific repos, so it's ready as a fallback if Nexus CE becomes untenable.

11. **Squid SSL bump scope**: If Squid is deployed as an auth proxy, will SSL bumping be enabled from the start? If so, adding package caching is straightforward. If not (CONNECT-only), the effort to enable SSL bump + CA distribution is significant and changes the cost/benefit calculus. This decision should be made early.

12. **Squid + Nexus coexistence**: Could Squid handle RPM and Maven caching (where metadata is simpler) while Nexus handles conda and PyPI (where metadata intelligence matters most)? This would reduce pressure on Nexus's component cap while keeping format-aware proxying where it's most needed. The clients already need different configuration per format, so splitting across proxies is transparent to users.

13. **HuggingFace Xet migration**: HuggingFace is migrating model storage from Git LFS to Xet (chunk-level deduplication). This breaks or degrades Nexus CE's HuggingFace proxy. The timeline and completeness of this migration is unclear. If Scout needs HuggingFace model caching, Olah or the shared NFS + `HF_HOME` approach are safer bets than Nexus for now.

14. **Harbor as a future convergence point**: Harbor 2.13+ supports ML model artifacts (OCI), and conda OCI distribution is emerging. If both mature, Harbor could eventually cache container images + conda packages + ML models -- three of Scout's five proxy needs in one existing tool. This would significantly simplify the architecture but depends on upstream ecosystem maturity.
