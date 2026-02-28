# CI Security Scanning

Scout's CI pipeline includes several layers of automated security scanning. This document describes each tool, what it does, how findings surface, and how to tune the configuration when scanners are too noisy or too quiet.

## Overview

| Tool | What it scans | Workflow file | Blocks PRs? | Findings visible in |
|---|---|---|---|---|
| **CodeQL** (`security-extended`) | Source code (JS/TS, Python, Java, Actions) | `codeql.yml` | Yes | Security tab > Code scanning |
| **Trivy image scan** | Container images built in CI | `ci.yaml` (scan-images job) | Yes (configurable per image via `allow-failure`) | Security tab > Code scanning |
| **Semgrep** | K8s, YAML, Dockerfiles, secrets, OWASP | `security.yaml` | Yes (new findings only, via Require code scanning results) | Security tab, PR annotations |
| **Dependency review** | New dependencies introduced in a PR | `dependency-review.yaml` | Yes (critical vulns only) | PR comment |
| **Dependabot** | Application deps (npm, pip, gradle, Docker) via GitHub UI; GitHub Actions via `dependabot.yml` | GitHub UI + `dependabot.yml` | N/A (security-only PRs; version updates for Actions only) | Pull requests |
| **Renovate** | `versions.yaml` — Helm charts, Docker images, GitHub releases | `renovate.yaml` | N/A (security-only PRs) | Pull requests, Dependency Dashboard issue |

## Files

```
.github/
├── actions/
│   └── trivy-scan-image/
│       └── action.yaml          # Composite action: download artifact → Trivy scan → SARIF upload → gate
├── workflows/
│   ├── ci.yaml                  # build-and-upload + scan-images matrices, publish gated on scans
│   ├── codeql.yml               # CodeQL with security-extended queries
│   ├── dependency-review.yaml   # GitHub-native dependency diff on PRs
│   ├── security.yaml            # Semgrep
│   └── renovate.yaml            # Self-hosted Renovate for versions.yaml
├── dependabot.yml               # Dependabot: GitHub Actions version updates only
renovate.json5                   # Renovate config (custom regex manager for versions.yaml)
.semgrepignore                   # Excluded paths for Semgrep
```

## Prerequisites

### CodeQL: disable default setup

The `codeql.yml` workflow is an "advanced setup". If the repository also has CodeQL's **default setup** enabled in the GitHub UI, SARIF uploads will fail with: _"CodeQL analyses from advanced configurations cannot be processed when the default setup is enabled"_.

To fix: **Settings > Security > Advanced Security > Code scanning > CodeQL analysis** — switch from "Default" to "Not set".

### GitHub Advanced Security (private repos)

Third-party SARIF uploads (Trivy, Semgrep) require **GitHub Advanced Security** to be enabled for private repos. Without it, uploads succeed silently but findings don't appear in the Security tab. CodeQL is exempt since it's GitHub-native.

Check: **Settings > Security > Advanced Security**.

### Rulesets: require status checks

The repository ruleset for `main` (**Settings > Rules > Rulesets**):

Under **Require status checks to pass**, add the workflow job names:

| Check name | Source | What it catches |
|---|---|---|
| `deploy-and-test` | `ci.yaml` | Build/test failures (already present) |
| `CodeQL` | `codeql.yml` | CodeQL analysis failures (already present) |
| `Security Scanning / semgrep` | `security.yaml` | Semgrep job crash or config error |
| `scan-images` | `ci.yaml` | Trivy image scan job failures |
| `Dependency Review / dependency-review` | `dependency-review.yaml` | Dependency review failure (also gates via exit-code on critical vulns) |

Additionally, under **Require code scanning results**, add Semgrep to block PRs that introduce new findings:

| Tool | Security alerts | Alerts |
|---|---|---|
| **CodeQL** | High or higher | Errors |
| **Semgrep** | High or higher | Errors |

This provides diff-aware gating — only findings *new* in a PR block the merge, not pre-existing ones. Tools appear in the dropdown after their first SARIF upload to the repository.

Trivy image scan also uploads SARIF but does not need to be added here — it gates via its own exit-code check in the status checks above.

## How each scanner works

### CodeQL (`security-extended`)

CodeQL runs GitHub's static analysis on push to `main`, PRs to `main`, and weekly. The `security-extended` query pack adds ~135 security queries covering 35 additional CWEs beyond the default set, with minimal false positives. It covers JavaScript/TypeScript, Python, Java/Kotlin, and GitHub Actions.

**Configuration**: `.github/workflows/codeql.yml`, line with `queries: security-extended`.

### Trivy image scanning

A separate `scan-images` matrix job in `ci.yaml` runs **in parallel** with `deploy-and-test`, both triggered after `build-and-upload` completes. This keeps scanning off the critical path.

```
build-and-upload (20m) ──→ scan-images (~2m, parallel) ──→ publish
                        └──→ deploy-and-test (20m)      ──┘
```

Each matrix entry attempts to download the image tarball artifact. If the image wasn't rebuilt (no file changes and image already in registry), the download is skipped gracefully and the scan is a no-op.

The composite action (`.github/actions/trivy-scan-image/action.yaml`) does a single Trivy invocation per image:

1. **Configure** — checks for a per-image `.trivyignore.yaml` in the subproject directory and generates a Trivy config if found.
2. **Single JSON scan** — Trivy scans once, outputting JSON. The Trivy DB is cached automatically by the trivy-action.
3. **SARIF conversion** — `trivy convert` produces SARIF from the JSON. The SARIF is post-processed with `jq` to tag the tool name and alert messages with the image name (e.g., "Trivy (launchpad)", "[launchpad] CVE description...") so findings are identifiable in the Security tab. The tagged SARIF is uploaded to GitHub.
4. **Gate check** — a shell step parses the JSON for fixable vulnerabilities and fails the job if any exist at CRITICAL or HIGH severity. Images with `allow-failure: true` in the matrix still scan and report but don't block the build.

The `publish` and `publish-demo` jobs require `scan-images` in their `needs:` array, so a failed or cancelled scan blocks publishing.

All third-party action references are pinned to commit hashes (not tags) to prevent supply chain attacks. Dependabot's `github-actions` ecosystem in `dependabot.yml` keeps these pins current.

**Images scanned**: `hl7log-extractor`, `hl7-transformer`, `pyspark-notebook`, `embedding-notebook` (`allow-failure`), `launchpad`, `superset`, `keycloak`.

### Semgrep

Runs in the `security.yaml` workflow inside the official `semgrep/semgrep` container (version-pinned to avoid unexpected breakage from `latest`). Current rule packs:

- `p/default` — general security rules
- `p/secrets` — hardcoded credentials, API keys
- `p/docker` — Dockerfile best practices
- `p/kubernetes` — Kubernetes manifest rules
- `p/owasp-top-ten` — OWASP Top 10 coverage

Semgrep runs once in SARIF mode (`--sarif --output semgrep.sarif`). A follow-up step parses the SARIF with `jq` to print a human-readable summary of findings to the workflow log. The SARIF is uploaded to GitHub's Security tab, which handles diff-aware reporting — new findings in a PR appear as inline annotations, while pre-existing findings are tracked in the Security tab.

### Dependency review

The `dependency-review.yaml` workflow uses GitHub's first-party `actions/dependency-review-action`. On every PR, it diffs the dependency graph between the base and head commits and blocks the PR if any new dependency introduces a known critical vulnerability. It posts a summary comment on every PR (`comment-summary-in-pr: always`).

License checking is disabled (`license-check: false`) because the action's license detection relies on GitHub's dependency graph metadata, which has poor coverage for Python packages and GitHub Actions (most show as "Unknown License"). A dedicated license compliance tool would be more reliable for this.

This only reviews **newly added or changed** dependencies — existing dependencies are not flagged.

### Dependabot

Dependabot handles two concerns:

1. **Application dependency CVEs** — configured via GitHub UI settings (not `dependabot.yml`). Dependabot automatically detects manifest files, builds the dependency graph, and opens PRs for dependencies with known CVEs.
2. **GitHub Actions version updates** — configured in `.github/dependabot.yml`. Actions pinned to commit hashes aren't tracked by the Advisory Database, so version updates are needed to keep them current. This is the only ecosystem with version-update PRs enabled.

**Required GitHub settings** (**Settings > Security > Advanced Security**):

1. **Dependency graph** — must be enabled. GitHub automatically detects manifest files (`package.json`, `pyproject.toml`, `requirements.txt`, `build.gradle`, `Dockerfile`, etc.) and builds the dependency graph. This is the foundation for all Dependabot features.
2. **Dependabot alerts** — must be enabled. Matches the dependency graph against the GitHub Advisory Database and creates alerts for known CVEs.
3. **Dependabot security updates** — enable this to have Dependabot automatically open PRs to fix every alert with an available patch. For more granular control (e.g., auto-dismiss low-severity alerts, target specific ecosystems), leave this disabled and configure **Dependabot rules** instead (**Settings > Security > Advanced Security > Dependabot rules**).

Dependabot does **not** monitor `ansible/group_vars/all/versions.yaml` — that file uses custom YAML keys that no standard package ecosystem can parse, so it is handled by Renovate (see below).

### Renovate

Configured in `renovate.json5` and runs as a self-hosted GitHub Action (`.github/workflows/renovate.yaml`) weekly on Mondays at 06:00 UTC.

Renovate monitors `ansible/group_vars/all/versions.yaml` for infrastructure dependencies with known CVEs: Helm charts, Docker images, and GitHub releases (~40 pinned versions). It uses a custom regex manager that reads `# renovate:` annotation comments above each version line to determine the datasource, package name, and registry URL.

PRs are only opened for dependencies with known CVEs (`enabled: false` globally, `vulnerabilityAlerts.enabled: true`). The Dependency Dashboard issue still lists all detected dependencies and available updates for visibility.

**Key configuration:**
- `enabled: false` + `vulnerabilityAlerts.enabled: true` — only opens PRs for dependencies with known CVEs (via OSV.dev)
- `dependencyDashboard: true` — creates a "Dependency Dashboard" GitHub issue listing all detected dependencies and their update status
- `prConcurrentLimit: 5` — limits concurrent open PRs
- `automerge: false` — all updates require human review
- `enabledManagers: ["custom.regex"]` — only manages `versions.yaml`; standard ecosystems are handled by Dependabot

**Authentication:** Uses a dedicated GitHub App (`scout-renovate`) via `RENOVATE_APP_ID` and `RENOVATE_APP_PRIVATE_KEY` repository secrets. The app needs Contents (R/W), Issues (R/W), Pull requests (R/W), and Metadata (R) permissions.

**Adding a new dependency to `versions.yaml`:** Add a `# renovate:` comment above the version line:

```yaml
# renovate: datasource=helm registryUrl=https://example.com/charts depName=my-chart
my_chart_version: ~1.2.0
```

See `renovate.json5` for the full regex pattern and ADR 0014 for the design decision.

## Reviewing findings

### On the pull request

Source-code scanners (CodeQL, Semgrep) post **inline annotations** on the PR diff for new findings, showing the rule ID, severity, and description directly on the affected lines. These appear in the "Files changed" tab. Trivy image scan findings appear in the Security tab but not as inline annotations since they reference container packages, not source lines.

### In the GitHub Security tab

All SARIF-based scanners (CodeQL, Trivy, Semgrep) also report to **Security tab > Code scanning alerts**, filterable by tool name.

**Important**: The Security tab defaults to showing findings for the `main` branch. To see findings from a PR branch, use the branch dropdown filter.

Useful filters in the code scanning alerts view:

- `is:open` — all open findings (default)
- `tool:semgrep` — findings from Semgrep only
- `tool:trivy` — findings from Trivy only
- `pr:123` — findings introduced in a specific PR
- `is:open tool:semgrep pr:123` — combine filters to narrow results

To dismiss a finding, click into it and select **Dismiss alert** with a reason (false positive, won't fix, or used in tests). Dismissals persist across future runs.

### In workflow logs

Each scanner also prints findings directly in the GitHub Actions workflow log:

- **Trivy image scan**: When gating is enabled, the gate step prints a table of fixable vulnerabilities (severity, CVE ID, package, installed vs. fixed version), prefixed with the image name.
- **Semgrep**: A `jq` step prints each finding's level, file:line, rule ID, and message excerpt.
- **CodeQL**: Findings appear in the "Perform CodeQL Analysis" step output.

To view: go to the workflow run in the **Actions tab**, click the relevant job, and expand the step.

## Triaging findings

When scanners are first enabled, expect a large initial set of findings from pre-existing code. These are not regressions — the scanners are making existing issues visible.

**Recommended approach:**

1. **Triage by rule, not by file** — sort by rule ID in the Security tab. A handful of rules typically produce most findings. Fixing the pattern is faster than fixing files individually.
2. **Prioritize by severity** — fix CRITICAL/HIGH first, track MEDIUM in the backlog.
3. **Dismiss false positives** — use "Dismiss" with a reason in the Security tab. This persists across runs.
4. **Don't block the initial merge** — the PR-level checks prevent regressions. The existing findings are tracked in the Security tab and can be addressed over time.

## Tuning and common modifications

### Suppressing a Trivy CVE

Each subproject can have its own `.trivyignore.yaml` alongside its `Dockerfile`. The composite action automatically detects and uses it if present. For example:

```
helm/jupyter/embedding-notebook/
├── Dockerfile
├── requirements.txt
└── .trivyignore.yaml    # CVE suppressions for this image only
```

Example ignore file:

```yaml
vulnerabilities:
  - id: CVE-2025-32434
    statement: "torch pinned at 2.0.1; upgrade to 2.6.0 pending validation"
```

Images without a `.trivyignore.yaml` in their subproject directory run with no suppressions.

### Changing Trivy severity thresholds

In the composite action (`.github/actions/trivy-scan-image/action.yaml`), the default severity is `CRITICAL,HIGH`. To scan only for CRITICAL:

```yaml
- uses: ./.github/actions/trivy-scan-image
  with:
    image-name: my-image
    severity: 'CRITICAL'
```

### Adding or removing Semgrep rule packs

Edit the `semgrep scan` command in `.github/workflows/security.yaml`:

```bash
semgrep scan --config p/default --config p/secrets --config p/docker --config p/kubernetes --config p/owasp-top-ten --sarif --output semgrep.sarif
```

Add `--config p/<pack>` for each new pack. After adding a pack, review findings in a test branch before merging to `main` to assess noise.

### Excluding paths from Semgrep

Edit `.semgrepignore` (gitignore syntax):

```
tests/
docs/
*.md
```

### Making dependency review block on HIGH severity (not just CRITICAL)

In `.github/workflows/dependency-review.yaml`:

```yaml
- uses: actions/dependency-review-action@v4
  with:
    fail-on-severity: high  # was 'critical'
```

### Adding a new container image to scanning

Add an entry to the `build-and-upload` matrix in `.github/workflows/ci.yaml`. The `scan-images` job reuses the same matrix via YAML anchor (`*image-matrix`), so no second edit is needed:

```yaml
strategy:
  matrix:
    include: &image-matrix
      # ... existing entries ...
      - image-name: new-image
        subproject: path/to/new-image
```

To allow an image to fail scanning without blocking the build (scan and report only), add `allow-failure: true`:

```yaml
      - image-name: new-image
        subproject: path/to/new-image
        allow-failure: true
```

### Upgrading CodeQL query pack

In `.github/workflows/codeql.yml`, the `queries` field controls the query pack. Options:

- `security-extended` (current) — default + ~135 additional security queries
- `security-and-quality` — all security queries + code quality rules (significantly noisier)

## Scheduled scans

- **CodeQL**: Tuesdays at 01:36 UTC (configured in `codeql.yml`)
- **Semgrep**: Mondays at 06:00 UTC (configured in `security.yaml`)
- **Renovate**: Mondays at 06:00 UTC (configured in `renovate.yaml`)
- **Dependabot security updates**: Run continuously as new advisories are published (managed by GitHub)
- **Dependabot version updates**: Weekly for GitHub Actions only (configured in `dependabot.yml`)

Scheduled scans catch newly disclosed CVEs and rule updates between code changes.
