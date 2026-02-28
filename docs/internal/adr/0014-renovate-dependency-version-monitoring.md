# ADR 0014: Dependency CVE Monitoring via Renovate and Dependabot

**Date**: 2026-02
**Status**: Proposed
**Decision Owner**: TAG Team

## Context

Scout pins ~40 external dependency versions in `ansible/group_vars/all/versions.yaml` — Helm charts, container images, K3s, operators — used by Ansible roles to deploy the platform. The file header says "Monitored by Renovate" but no monitoring configuration existed.

Scout also builds 7 custom Docker images with their own application dependencies (npm, pip, gradle) that are not monitored for available updates by any tool.

The `appsec` branch (ADR pending) adds Dependabot for GitHub Actions version pinning, plus Trivy/Semgrep/CodeQL scanning for CVEs in built images and source code. However, none of these tools detect **available updates** for the specific versions pinned in `versions.yaml`, nor do they monitor application dependencies for available updates (only CVEs in built images via Trivy).

### Gap Analysis

| What needs monitoring | Existing coverage | Gap |
|---|---|---|
| `versions.yaml` — Helm charts, Docker images, GitHub releases | None | No tool detects available updates or CVEs for pinned versions |
| Application dependencies — npm, pip, gradle | Trivy (CVEs in built images only) | No available-update PRs; CVEs only detected post-build |
| Dockerfile base images | Trivy (CVEs in built images only) | No available-update PRs |
| GitHub Actions | Dependabot (version updates) | None — already covered |

### Tool Capabilities

| Capability | Renovate | Dependabot |
|---|---|---|
| Parse custom YAML files (like `versions.yaml`) | Yes — custom regex managers | No — only supports standard ecosystems |
| Standard ecosystems (npm, pip, gradle, Docker) | Yes | Yes |
| CVE detection | Yes — OSV.dev integration | Yes — GitHub Advisory Database |
| Self-hosted option | Yes — GitHub Action | No — GitHub-hosted only |
| Dependency Dashboard (overview issue) | Yes | No |

## Decision

**Use two complementary tools, each handling what it does best, scoped to CVE-only PRs:**

| Tool | Scope | Why this tool |
|---|---|---|
| **Renovate** (self-hosted GitHub Action) | `versions.yaml` — Helm charts, Docker images, GitHub releases | Only tool that can parse custom YAML files via regex managers |
| **Dependabot** (expand existing config) | Application dependencies — npm, pip, gradle, Docker base images | Native support for standard package ecosystems; already in use for GitHub Actions |

Both tools are configured to only open PRs for dependencies with known CVEs — not for routine version updates. Neither tool auto-merges; all PRs require human review.

### Renovate Configuration

Renovate runs as a self-hosted GitHub Action (`renovate.yaml`) using a dedicated GitHub App (`scout-renovate`) for authentication, following the same pattern as existing apps (`scout-release`, `scout-copyright-updater`).

The Renovate config (`renovate.json5`) uses a single custom regex manager scoped to `versions.yaml`. Each dependency in the file gets a standardized `# renovate:` comment annotation specifying:
- `datasource` — `helm`, `docker`, or `github-releases`
- `depName` — the package/chart/image name
- `registryUrl` — (Helm only) the chart repository URL
- `versioning` — (optional) override for non-semver schemes
- `extractVersion` — (optional) regex to extract version from tag (e.g., K3s `v1.34.1+k3s1`)

Renovate is configured with:
- `enabled: false` + `vulnerabilityAlerts.enabled: true` — only opens PRs for dependencies with known CVEs via OSV.dev; the Dependency Dashboard still lists all available updates for visibility
- `dependencyDashboard: true` — creates a GitHub issue listing all detected dependencies and pending updates
- `prConcurrentLimit: 5` — limits open PRs to avoid noise
- `enabledManagers: ["custom.regex"]` — only manages `versions.yaml`; standard ecosystems are handled by Dependabot

### Dependabot Configuration

Dependabot handles two concerns with different mechanisms:

1. **Application dependency CVEs** — configured via GitHub UI settings, not `dependabot.yml`. GitHub automatically detects manifest files (`package.json`, `pyproject.toml`, `requirements.txt`, `build.gradle`, `Dockerfile`) and builds the dependency graph. Dependabot alerts and security updates then open PRs for known CVEs.

   **Required GitHub settings** (**Settings > Security > Advanced Security**):
   - **Dependency graph** — enabled (foundation for all Dependabot features)
   - **Dependabot alerts** — enabled (matches dependency graph against GitHub Advisory Database)
   - **Dependabot security updates** — enabled for automatic PRs, or use **Dependabot rules** for granular control

2. **GitHub Actions version updates** — configured in `.github/dependabot.yml` with only the `github-actions` ecosystem. Actions pinned to commit hashes aren't tracked by the Advisory Database, so version-update PRs are needed to keep them current.

The Superset Dockerfile's inline `uv pip install` packages are extracted to a `requirements.txt` so the dependency graph can detect them.

### Scope Separation

Renovate and Dependabot manage disjoint scopes with no overlap:
- **Renovate**: `versions.yaml` only (custom regex manager, CVE-only PRs via OSV.dev)
- **Dependabot**: Application dependency CVEs (via GitHub UI settings) + GitHub Actions version updates (via `dependabot.yml`)

### Dependencies Not Monitored

| Dependency | Reason |
|---|---|
| `cassandra_server_version` | Managed by K8ssandra cass-operator; not a direct image pull |
| `orthanc_image` / `orthanc_version` | Pinned to `latest`; not a specific version Renovate can track |

## Alternatives Considered

### Summary

| Alternative | Verdict |
|---|---|
| **1. Renovate + Dependabot (Selected)** | **Selected — each tool handles what it does best** |
| 2. Renovate only (for everything) | Rejected — Dependabot already in use; simpler config for standard ecosystems |
| 3. Dependabot only + proxy files | Rejected — cannot parse `versions.yaml`; proxy files add maintenance burden |
| 4. Hosted Renovate (Mend.io app) | Rejected — requires third-party service registration |
| 5. Custom GitHub Action | Rejected — reinvents version comparison logic |

### Alternative 2: Renovate Only

Use Renovate for both `versions.yaml` and standard application dependencies, replacing Dependabot entirely.

**Pros:**
- Single tool for all dependency monitoring
- More powerful configuration (grouping, scheduling, auto-merge rules)
- Dependency Dashboard covers everything

**Cons:**
- Dependabot is already configured and understood by the team
- Dependabot's GitHub-native integration (Advisory Database, security updates) is tighter than Renovate's OSV.dev
- More complex Renovate config to maintain

**Verdict:** Rejected. The added complexity of configuring Renovate for standard ecosystems doesn't justify replacing a working Dependabot setup.

### Alternative 3: Dependabot Only + Proxy Files

Create proxy package manifest files (e.g., a `package.json` or `Dockerfile`) that mirror the versions in `versions.yaml`, allowing Dependabot to monitor them.

**Pros:**
- Single tool
- No self-hosted infrastructure

**Cons:**
- Proxy files must be kept in sync with `versions.yaml` manually
- Dependabot cannot monitor Helm chart versions (no ecosystem support)
- Fragile and error-prone

**Verdict:** Rejected. Maintenance burden of proxy files outweighs the benefit of a single tool.

### Alternative 4: Hosted Renovate (Mend.io App)

Install the Renovate GitHub App from the GitHub Marketplace, hosted by Mend.io.

**Pros:**
- No self-hosted workflow to maintain
- Automatic updates to Renovate itself

**Cons:**
- Requires Mend.io account registration and third-party service dependency
- Less control over execution schedule and environment
- Data leaves the GitHub environment

**Verdict:** Rejected. Self-hosted Renovate via GitHub Action provides equivalent functionality without third-party dependencies, following the existing GitHub App pattern.

### Alternative 5: Custom GitHub Action

Write a custom GitHub Action that parses `versions.yaml`, checks registries for newer versions, and opens PRs.

**Pros:**
- Full control over behavior
- No external tool dependency

**Cons:**
- Significant development effort to implement version comparison, PR creation, and registry API integration
- Must handle rate limiting, authentication, error recovery
- No CVE detection without additional integration
- Ongoing maintenance burden

**Verdict:** Rejected. Renovate already solves this problem comprehensively.

## Consequences

### Positive

- All ~40 infrastructure dependencies in `versions.yaml` are monitored for known CVEs via OSV.dev (Renovate)
- All application dependencies (npm, pip, gradle) and Dockerfile base images are monitored for known CVEs via GitHub Advisory Database (Dependabot security updates)
- PRs are only opened for dependencies with fixable CVEs — no noise from routine version updates
- The Dependency Dashboard provides a single overview of all detected infrastructure dependencies and available updates (for manual review)
- No auto-merge — all updates require human review and testing

### Negative

- Two dependency monitoring tools to understand and maintain
- Self-hosted Renovate requires a GitHub App and repository secrets (`RENOVATE_APP_ID`, `RENOVATE_APP_PRIVATE_KEY`)
- Dependabot security updates require the feature to be enabled in the GitHub UI (**Settings > Security > Advanced Security > Dependabot security updates**)
- PR volume is limited to CVE-affected dependencies; `prConcurrentLimit: 5` further mitigates noise for Renovate
- `# renovate:` annotations in `versions.yaml` add visual noise; they also must be kept in sync when adding new dependencies

### Operational

- **Adding a new dependency to `versions.yaml`**: Add a `# renovate:` comment above the version line with the appropriate `datasource`, `depName`, and optional `registryUrl`/`versioning`/`extractVersion`
- **Adding a new application dependency**: No config changes needed — GitHub's dependency graph automatically detects standard manifest files. Verify the dependency appears under **Insights > Dependency graph**.
- **GitHub UI setup**: Enable Dependency graph, Dependabot alerts, and Dependabot security updates (or configure Dependabot rules) under **Settings > Security > Advanced Security**
- **GitHub App setup**: Create the `scout-renovate` GitHub App, install on the repository, and add `RENOVATE_APP_ID` and `RENOVATE_APP_PRIVATE_KEY` as repository secrets (see setup instructions in this ADR's parent PR)
- **Verifying Renovate**: After merging, trigger the workflow manually via `workflow_dispatch` and verify the Dependency Dashboard issue is created

## Related

- **ADR 0012**: Security Scan Response and Hardening — security headers and scan findings
- `docs/internal/ci-security-scanning.md` — CI security scanning overview (Trivy, CodeQL, Semgrep, Dependabot)
- `renovate.json5` — Renovate configuration
- `.github/dependabot.yml` — Dependabot configuration (GitHub Actions version updates only)
- `.github/workflows/renovate.yaml` — Renovate GitHub Actions workflow
