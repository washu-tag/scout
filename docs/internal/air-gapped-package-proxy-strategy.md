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

The common thread: **we need pull-through proxies that cache artifacts on first request and serve them transparently to production nodes.** Harbor already does this for container images. This report investigates extending that pattern to Python/conda packages, Maven artifacts, and RPM repositories.

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

| Tool | Conda | PyPI | Maven | RPM/yum | Container Images | Free | Pull-Through |
|------|-------|------|-------|---------|-----------------|------|-------------|
| **Nexus CE** | Yes | Yes | Yes | Yes | Yes | Yes* | Yes |
| **Pulp** | **No** | Yes | Yes | Yes | Yes | Yes | Yes |
| **Artifactory Pro** | Yes | Yes | Yes | Yes | Yes | No ($6k+/yr) | Yes |
| **Artifactory OSS** | No | No | Yes | No | No | Yes | Yes |
| **ProGet** | Yes | Yes | Yes | Yes | Yes | Free tier | Yes |
| **Harbor** | No | No | No | No | Yes | Yes | Yes |
| **devpi** | No | Yes | No | No | No | Yes | Yes |

\* Nexus CE has usage caps: **40,000 total components** and **100,000 requests/day**. When exceeded, new component additions are blocked.

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

- **Legacy chart** (`sonatype/nexus-repository-manager` from `helm3-charts`): Deprecated, supports up to Nexus 64.2.0, uses an embedded OrientDB database. Works for CE but will not receive updates. Sonatype warns that the embedded database can corrupt under certain Kubernetes pod termination conditions.

- **Current chart** (`sonatype/nxrm-ha` from `helm3-charts`): The only supported chart going forward. Requires an external PostgreSQL database. However, it **hardcodes** `-Dnexus.licenseFile=${LICENSE_FILE}` in the StatefulSet template, which causes CE to fail at startup because no license file exists.

- **Workaround for CE on `nxrm-ha`** ([sonatype/nexus-public#926](https://github.com/sonatype/nexus-public/issues/926)): The StatefulSet template can be patched to conditionally include the license flag only when a license secret is configured. The fix checks `.Values.secret.license.licenseSecret.enabled`, `.Values.secret.license.existingSecret`, and cloud secret manager flags -- if none are true, the `-Dnexus.licenseFile` parameter is omitted. With this patch, CE starts successfully on the `nxrm-ha` chart with PostgreSQL.

**Recommended deployment approach for production:**

1. Vendor/fork the `nxrm-ha` chart into the Scout repo (e.g., `helm/nexus/`)
2. Apply the license-conditional patch from issue #926
3. Deploy a small PostgreSQL instance on the staging K3s cluster (via CloudNativePG, which Scout already uses for the production cluster, or a simple StatefulSet)
4. This gives: supported chart, PostgreSQL-backed storage (no corruption risk), CE compatibility, and a clear upgrade path to Pro if the 40K component cap becomes a problem

For POC/evaluation, the deprecated `nexus-repository-manager` chart is acceptable.

#### Pulp Project

Originally developed by Red Hat; powers Red Hat Satellite and Microsoft's Linux repositories. Plugin-based architecture with excellent RPM support (its original purpose).

**Supported formats:** RPM (very mature), containers, PyPI, Maven, Debian, Ansible, npm, Hugging Face models, and more via plugins. **No conda plugin exists.**

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

#### ProGet (Inedo)

Supports conda, PyPI, RPM, Docker, Maven, npm. Free tier available. .NET-based, which is unusual in Linux/K8s environments. Less ecosystem presence but functional. Worth considering as a Nexus alternative if the 40K cap becomes a problem.

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

### Option D: Artifactory Pro (everything)

```
Internet ← Artifactory Pro (staging) ← Production K3s (everything)
```

**Pros:** No usage caps, no limitations, every format, one tool, commercial support.

**Cons:** $6,000+/year. Replaces working Harbor setup unnecessarily.

**Verdict: Only if budget allows and simplicity is paramount.**

### Recommendation

**Start with Option B (Harbor + Nexus CE).** It adds one new tool (Nexus) to handle four new proxy needs (conda, PyPI, Maven, RPM). Keep Harbor for containers. This is the minimum viable architecture.

If the Nexus CE component limit becomes a problem:
1. First try: clean up unused cached components (Nexus has cleanup policies)
2. If that's insufficient: split RPM to Pulp (Option C), since RPM repos have the most packages and are the easiest to separate
3. If that's still insufficient: evaluate Nexus Pro or Artifactory Pro

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

**Network policy**: The existing Jupyter network policy defines an explicit egress allowlist (MinIO, Hive, Trino, Ollama). Since specifying `egress` rules in a NetworkPolicy implicitly denies all non-matching traffic -- including traffic to external IPs -- an egress rule must be added for the staging node even though it's outside the cluster. The `namespaceSelector` approach won't work for cross-cluster traffic; use an `ipBlock` CIDR instead:

```yaml
singleuser:
  networkPolicy:
    egress:
      - to:
          - ipBlock:
              cidr: <staging-node-ip>/32
        ports:
          - port: 443
```

This configuration would be templated into `ansible/roles/jupyter/templates/values.yaml.j2` and toggled with an inventory variable (e.g., `jupyter_package_proxy_url`).

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

1. Vendor the `nxrm-ha` Helm chart into the Scout repo and apply the CE license-conditional patch from [sonatype/nexus-public#926](https://github.com/sonatype/nexus-public/issues/926)
2. Deploy a small PostgreSQL instance on the staging K3s cluster (required by the `nxrm-ha` chart)
3. Deploy Nexus CE using the patched chart with Traefik ingress on the staging node
4. Configure proxy repositories:
   - Conda: `conda-forge`, `conda-defaults`
   - PyPI: `pypi-proxy` → `https://pypi.org/`
   - Maven: `maven-central` → `https://repo1.maven.org/maven2/`
   - Yum: `rancher-k3s`, `nvidia-container`, `epel`
5. Test connectivity from production cluster to staging proxy
6. Add Ansible role for Nexus deployment (alongside existing Harbor staging role)
7. Monitor component usage against the 40K cap

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

1. **Nexus component cap**: The 40K component limit is the biggest risk. With pull-through proxying (only caching what's requested), we may stay under this for a long time, but it needs monitoring. If it becomes a problem, options are: cleanup policies, splitting RPM to Pulp, or upgrading to Nexus Pro.

2. **Proxy availability**: If the staging node or Nexus goes down, no new packages can be installed (existing cached packages are still served). Nexus stores its cache on disk (should use a persistent volume), and the staging node is already a single point of failure for Harbor. This doesn't change the risk profile much.

3. **First-run experience**: If users need to create a conda environment before they can do anything, the first notebook experience degrades. The postStart script could auto-create a default environment, but `conda env create` for a full data science stack takes several minutes. Consider pre-warming the proxy cache during deployment.

4. **Spark version coupling**: Spark, Delta Lake, Hadoop, and the AWS SDK have tight version interdependencies. Making these user-configurable risks subtle breakage. Spark JARs should probably remain admin-controlled even if Python packages become user-controlled.

5. **Storage for user environments**: Conda environments can be large (several GB for ML stacks). The current default PVC may need to increase, or we need to document environment cleanup procedures.

6. **Security posture**: With a proxy, users can install arbitrary packages (from the proxy cache). The air gap still prevents reaching the internet directly, but the proxy cache grows organically. Is this acceptable to the security team? Nexus has features for blocking specific packages if needed.

7. **Should Nexus replace Harbor?** Probably not. Harbor is purpose-built for containers (vulnerability scanning, content trust, CNCF graduated project) and is already deployed. The component cap concern also argues against consolidating Docker images into Nexus. But it's worth noting that Nexus *can* do it if simplification is ever prioritized over features.

8. **Phase 2 scope**: The RPM proxy work could be done independently of the Jupyter work. It's a simpler, lower-risk change that immediately simplifies the Ansible codebase. Consider prioritizing it.
