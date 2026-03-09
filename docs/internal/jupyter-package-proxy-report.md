# Jupyter Notebook Image Strategy: Package Proxies and Runtime Customization

*Date: 2026-03-09*

## Problem Statement

Scout's Jupyter notebook images are large, slow to build/pull/cache, and impose rigid version constraints on users. The current approach bakes everything into the image at build time because the production environment is air-gapped. This creates several problems:

1. **Image size and build time** slow CI, releases, and deployment
2. **Version lock-in**: Users who need different Python/package versions require a new image build
3. **Multiple image variants** (standard, embedding) multiply the maintenance burden
4. **Stale dependencies**: Spark, JARs, and Python packages have hard-coded versions that trip CVE scanners and are difficult to update
5. **No user agency**: Users cannot install packages they need without admin intervention

## Current State

### Images

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

Users access the Delta Lake via PySpark. The Quickstart notebook demonstrates this pattern:

```python
from pyspark.sql import SparkSession
spark = SparkSession.builder.appName("scout").enableHiveSupport().getOrCreate()
df = spark.read.table("reports")
```

Spark requires:
- The base `pyspark-notebook` image (includes JVM + PySpark)
- Delta Lake JARs (`delta-spark`, `delta-storage`)
- Hadoop/S3 JARs (`hadoop-aws`, `hadoop-common`, `hadoop-hdfs`, `aws-java-sdk-bundle`)
- `smolder` (custom HL7 parsing JAR)
- Runtime Spark configuration (`spark-defaults.conf`) injected via ConfigMap

Users also access Trino directly via the `trino` Python package for SQL queries without Spark.

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

## Proposal: Package Proxy on Staging Node

Run a package proxy on the staging node (which has internet access). Production Jupyter pods pull packages through it at runtime instead of baking everything into the image.

### Architecture

```
Internet ← Staging Node (Package Proxy) ← Production K3s (Jupyter Pods)
              Nexus / similar tool
              - conda channels (conda-forge, defaults)
              - PyPI index
              - (optional) Maven Central for JARs
```

The proxy operates as a **pull-through cache**: packages are fetched from upstream on first request and cached locally. Subsequent requests are served from cache. This means:
- No need to pre-mirror entire channels
- Only packages actually used are cached
- Works transparently with standard conda/pip commands

### Tool Recommendation: Sonatype Nexus Repository (Community Edition)

After evaluating available tools, **Nexus Community Edition** is the strongest candidate:

| Tool | Conda Pull-Through | PyPI Pull-Through | Free | Notes |
|------|-------------------|-------------------|------|-------|
| **Nexus Community** | Yes | Yes | Yes | Most mature free option; Helm chart available |
| Artifactory | Yes | Yes | No (Pro: $6k/yr) | Conda/PyPI require paid license |
| ProGet | Yes | Yes | Free tier | .NET-based; less common in Linux/K8s |
| Artifact Keeper | Yes | Yes | Yes (OSS) | Very new; Rust-based, lightweight but unproven |
| devpi | No | Yes | Yes (MIT) | PyPI only; no conda support |
| conda-mirror | Full mirror only | No | Yes | Not a proxy; requires mirroring entire channels |

**Why Nexus:**
- True pull-through proxy for both conda and PyPI (and Maven, which could help with JARs)
- Free Community Edition includes conda proxy support
- Official Helm chart for Kubernetes deployment
- Well-established in enterprise environments
- Fits Scout's existing staging/production pattern

**Tradeoffs:**
- Resource-heavy: ~4-5 GB RAM (JVM-based)
- Adds infrastructure to maintain on the staging node
- Each conda channel needs its own proxy repository (but they can be grouped)

### Transparent Client Configuration

The key advantage of a proxy is that users don't need to know about it. Configuration is injected into every notebook pod automatically.

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

The `channel_alias` setting is key: it rewrites channel name references to route through the proxy. When a user runs `conda install -c conda-forge numpy`, it resolves to the proxy URL automatically.

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

**Network policy**: Add an egress rule for singleuser pods to reach the proxy:

```yaml
singleuser:
  networkPolicy:
    egress:
      - to:
          - namespaceSelector:
              matchLabels:
                kubernetes.io/metadata.name: staging
        ports:
          - port: 8081
```

This configuration could be templated into the existing `ansible/roles/jupyter/templates/values.yaml.j2` and toggled with an inventory variable (e.g., `jupyter_package_proxy_url`).

---

## Impact on the Jupyter Image

With a package proxy in place, the image can be dramatically simplified:

### What stays in the image (must be baked in)

- **Base Jupyter environment**: The `jupyter/pyspark-notebook` base image (provides JVM, PySpark, conda, JupyterLab)
- **Spark JARs**: These are JVM dependencies loaded at Spark startup, not pip/conda packages. They must be present before any notebook runs. However, Nexus also proxies Maven Central, so a startup script could fetch them -- see "Launch-Time Customization" below.
- **nb_conda_kernels**: Enables user-created conda envs to appear as Jupyter kernels. This is core infrastructure, not a user package.
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

## The Spark Question

Spark is the most complex dependency and the hardest to move out of the image.

### Option A: Keep Spark in the image (recommended short-term)

Continue using `pyspark-notebook` as the base image. This provides:
- JVM (OpenJDK)
- PySpark
- Spark binaries and default JARs

The custom JARs (Delta, Hadoop-AWS, smolder) still need to be present. Options:
1. **Keep them in the Dockerfile** (status quo) -- simplest, but still hard-coded
2. **Fetch via init container or postStart hook from the Maven proxy** -- more flexible, but adds startup latency

### Option B: Remove Spark from the base image (longer-term consideration)

If users primarily need SQL access to the Delta Lake, **Trino via the `trino` Python package** is a lighter alternative:

```python
from trino.dbapi import connect
conn = connect(host="trino", port=8080, catalog="delta", schema="default")
cursor = conn.cursor()
cursor.execute("SELECT * FROM reports WHERE modality = 'CT' LIMIT 100")
df = pd.DataFrame(cursor.fetchall())
```

This eliminates the need for PySpark, JVM, and all Spark JARs. The base image could be `jupyter/scipy-notebook` instead of `jupyter/pyspark-notebook`, which is significantly smaller.

**Tradeoffs:**
- Breaks existing PySpark notebooks (the Quickstart and likely all user notebooks)
- Loses Spark-specific functionality (UDFs, MLlib, streaming)
- Trino is read-only against the Delta Lake; users cannot write back to it
- Some users may genuinely need Spark for heavy data processing

**A middle path**: Offer Spark as an optional profile. Users who need it select a Spark profile (larger image); users who only need SQL get the lightweight Trino-only profile.

### Option C: Spark JARs via Maven proxy

Nexus can proxy Maven Central. Instead of hard-coding JAR URLs in the Dockerfile, an init container or postStart hook could download the required JARs at startup:

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

This makes JAR versions configurable without rebuilding the image, and the URLs go through the proxy cache. However, it adds ~30-60s to pod startup for the initial download (cached after first pull by Nexus).

---

## Launch-Time Customization

JupyterHub provides several mechanisms for running setup scripts when a notebook pod starts.

### Mechanisms (from simplest to most flexible)

| Mechanism | Runs Before Notebook? | Per-User? | User Self-Service? |
|-----------|----------------------|-----------|-------------------|
| `lifecycleHooks.postStart` | No (async) | No | No |
| `initContainers` | Yes (guaranteed) | Limited | No |
| `profileList` | N/A (sets config) | Menu choice | Choose from list |
| `pre_spawn_hook` (Python) | Hub-side only | Yes | No |
| Mounted scripts via ConfigMap/PVC | Depends | Yes | Possible |

### Recommended Approach: Layered Customization

**Layer 1: Admin-controlled init container** (runs before notebook starts)

An init container that:
- Fetches Spark JARs from the Maven proxy (if using Option C above)
- Sets up base conda/pip proxy configuration
- Runs any admin-defined setup scripts from a ConfigMap

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

Check for a user-provided script in the persistent home directory and run it:

```yaml
singleuser:
  lifecycleHooks:
    postStart:
      exec:
        command:
          - /bin/sh
          - -c
          - |
            # Admin setup (quickstart samples, etc.)
            if [ ! -f /home/jovyan/.scout_quickstart ]; then
              cp -rn /opt/scout/samples/* /home/jovyan/Scout/ 2>/dev/null
              touch /home/jovyan/.scout_quickstart
            fi
            # User startup script (if exists)
            if [ -f /home/jovyan/.scout/startup.sh ]; then
              /bin/sh /home/jovyan/.scout/startup.sh
            fi
```

Users create `~/.scout/startup.sh` in their persistent home directory to automate environment setup:

```bash
#!/bin/bash
# Example user startup script
conda activate my-env || conda env create -f ~/my-env.yml
```

Since the home directory is a persistent PVC, this script survives pod restarts.

**Caveats:**
- `postStart` runs asynchronously -- the notebook UI may appear before the script finishes
- For scripts that must complete before the notebook is usable, an init container is better (but init containers don't have access to the user's PVC in a straightforward way)
- Long-running setup (e.g., `conda env create` with many packages) will cause the pod to appear "stuck" from the user's perspective

**Layer 3: Profile-based environment selection**

Use JupyterHub's `profileList` to offer distinct environment choices at spawn time:

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

Scout already uses `profileList` for CPU/memory sizing (small/medium/large). This extends it for environment selection.

### Domino-Style Comparison

For reference, Domino Data Lab's model has these layers:
1. Admin-curated base Docker image
2. User-customizable Dockerfile instructions (triggers rebuild)
3. Pre-run scripts (bash, runs at startup as root)
4. Project-level `requirements.txt` (pip install at launch)
5. Per-user environment variables (Vault-backed)

Scout can approximate this with:
1. Admin-curated base image (simplified, with proxy config)
2. ~~User-customizable Dockerfile~~ (replaced by conda environments via proxy)
3. `~/.scout/startup.sh` (user's persistent PVC)
4. `environment.yml` in project directory (conda env create at launch)
5. Per-user env vars via `pre_spawn_hook` (if needed)

The key difference: Domino rebuilds images; Scout avoids rebuilds entirely by using runtime package installation through the proxy.

---

## Implementation Sketch

### Phase 1: Deploy package proxy on staging

1. Deploy Nexus Community Edition on the staging node (Helm chart or standalone)
2. Configure conda proxy repositories (conda-forge, defaults)
3. Configure PyPI proxy repository
4. (Optional) Configure Maven Central proxy repository
5. Test connectivity from production cluster to staging proxy
6. Add Ansible role for Nexus deployment

### Phase 2: Configure Jupyter to use proxy

1. Add `.condarc` and `pip.conf` to Helm values via `extraFiles`
2. Add network policy egress rule for proxy access
3. Install `nb_conda_kernels` in the standard image (already in embedding image)
4. Configure `envs_dirs` for persistent user environments
5. Update the Quickstart notebook to demonstrate creating a conda environment
6. Ship a default `environment.yml` with the standard data science packages

### Phase 3: Slim down the image

1. Remove pre-installed data science packages from requirements.txt
2. Keep only JupyterLab core, `nb_conda_kernels`, and infrastructure packages
3. Merge the two Dockerfiles into one base image
4. Test that the default `environment.yml` recreates the expected environment via proxy

### Phase 4 (optional): Externalize Spark JARs

1. Configure Nexus Maven proxy
2. Create init container or startup script to fetch JARs
3. Make JAR versions configurable via ConfigMap
4. Remove hardcoded JAR URLs from Dockerfile

### Phase 5 (optional): Trino-only lightweight profile

1. Create a `scipy-notebook`-based image without Spark
2. Add as a profile option in `profileList`
3. Document the Trino-only workflow as an alternative to PySpark

---

## Open Questions

1. **Proxy availability**: If the staging node or Nexus goes down, no packages can be installed. Should we cache packages more aggressively (e.g., persistent volume for Nexus cache)? This is likely already handled by Nexus's built-in storage, but the staging node itself is a single point of failure.

2. **First-run experience**: If users need to create a conda environment before they can do anything, the first notebook experience degrades. The postStart script could auto-create a default environment, but `conda env create` for a full data science stack takes several minutes. Should we pre-warm the proxy cache during deployment?

3. **Spark version coupling**: Spark, Delta Lake, Hadoop, and the AWS SDK have tight version interdependencies. Making these user-configurable risks subtle breakage. Should Spark JARs remain admin-controlled even if Python packages become user-controlled?

4. **Storage for user environments**: Conda environments can be large (several GB for ML stacks). The current default PVC is 10 GB. This may need to increase, or we need to document environment cleanup procedures.

5. **Conda vs pip**: Should we standardize on conda environments (which can manage Python versions and non-Python dependencies) or allow mixed conda+pip? The `nb_conda_kernels` approach works best with conda environments.

6. **Security posture**: The current approach of baking everything in is more auditable. With a proxy, users can install arbitrary packages. The air gap still prevents reaching the internet directly, but the proxy cache grows organically. Is this acceptable to the security team?
