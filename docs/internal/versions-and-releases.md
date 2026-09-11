# Scout Versioning and Releases

This document describes Scout's versioning strategy and the automated release workflow.

## Versioning Strategy

Scout uses a **manual dispatch release workflow**. The workflow:

1. **Source files maintain dev versions** - no manual version bumps for day-to-day development
2. **Human triggers release via GitHub Actions** - specifying the version to release
3. **Version bump commit created at release time** - the repo contains a commit with release versions
4. **Automatic reset after release** - dev versions restored by running the update script
5. **Tag created on success** - the version tag only exists after everything succeeds

### Key Points

- Tags are created at the end of the release process, not the beginning
- This eliminates wasted version numbers from failed releases
- The `vX.Y.Z` tag points to the version bump commit
- Changelog is auto-generated from PR titles since the last release

## Development Versions

For day-to-day development on `main`, all Scout components use development version values:

| Component Type | Dev Version | Constraint |
|----------------|-------------|------------|
| Docker image tags | `latest` | None |
| npm packages | `latest` | None |
| Gradle builds | `latest` | None |
| VERSION files | `latest` | None |
| Helm chart `version` | `0.0.0-dev` | [SemVer 2](https://helm.sh/docs/topics/charts/) |
| Helm chart `appVersion` | `"latest"` | None (Scout apps only) |
| Python pyproject.toml | `"0.0.dev0"` | [PEP-440](https://peps.python.org/pep-0440/) |

CI publishes any changes to `main` with the `latest` Docker image tag.

**Note on constrained versions**:
- **Helm charts** require SemVer 2 compliant versions. `latest` is not valid; we use `0.0.0-dev`.
- **Python packages** require PEP-440 compliant versions. `latest` is not valid; we use `0.0.dev0`. A separate `VERSION` file containing `latest` controls the Docker image tag.

## Release Process

### Overview Diagram

```
Developer                    GitHub                        CI
    |                           |                           |
    |-- Trigger Release ------->|                           |
    |   (workflow_dispatch)     |                           |
    |   version: 2.1.0          |                           |
    |                           |                           |
    |                           |-- Release Workflow ------>|
    |                           |                           |
    |                           |     Validate version      |
    |                           |     Check tag doesn't exist
    |                           |          |                |
    |                           |          v                |
    |                           |     Version bump commit   |
    |                           |     (X.Y.Z in all files)  |
    |                           |     Push to branch        |
    |                           |          |                |
    |                           |          v                |
    |                           |     Build Workflow runs   |
    |                           |     (triggered by commit) |
    |                           |          |                |
    |                           |          v                |
    |                           |     Wait for build -------+---> [Build fails]
    |                           |          |                |           |
    |                           |          v                |           v
    |                           |     Publish images        |    main stays STAMPED
    |                           |     (non-main only)       |    (bump kept for a retry)
    |                           |          |                |
    |                           |          v                |
    |                           |     Create release        |
    |                           |     (auto-gen changelog)  |
    |                           |     Create vX.Y.Z tag     |
    |                           |          |                |
    |                           |          v                |
    |                           |     Reset to dev versions |
    |                           |     Push to branch        |
    |                           |                           |
    |<-- Release complete ------|                           |
```

> **Note**: The reset to dev versions step runs **only after the release succeeds** (`reset-dev` is gated on `needs.release.result == 'success'`). Any earlier failure leaves `main` stamped at `X.Y.Z`, deliberately, so the version bump commit survives for a retry. See [Design Decision: Reset Timing](#design-decision-reset-timing) for what that costs and how to get out of it.

### Triggering a Release

1. **Go to GitHub Actions** → **Release** workflow
2. **Click "Run workflow"**
3. **Enter the version** (e.g., `2.1.0`)
4. Optionally check **dry_run** to preview the changelog without releasing
5. **Click "Run workflow"**

### What the Workflow Does

1. **Validates** the version format and checks the tag doesn't already exist
2. **Searches git history** for an existing version bump commit (for idempotent re-runs)
3. **Updates version files** and commits the version bump to the branch (if not already done)
4. **Waits for the Build Workflow** to complete on HEAD (builds versioned artifacts)
5. **Publishes Docker images** to GHCR (non-main branches only — on main, the CI workflow's publish job handles this)
6. **Creates the GitHub release** with auto-generated changelog (if build succeeded)
7. **Creates the `vX.Y.Z` tag** pointing at HEAD (if build succeeded)
8. **Resets to dev versions** by running the update script and committing (always, regardless of build result)

### Result

- Release `v2.1.0` is published with changelog
- Docker images tagged `2.1.0` are available
- Tag `v2.1.0` points to the commit that was actually built and released
- Branch is back to dev versions (unless `skip_dev_reset` was checked)

### Post-Release Steps

- When upgrading a site to the new release, bump its pinned docs URL to
  match: `scout_docs_url: https://washu-scout.readthedocs.io/en/v2.1.0` in
  that site's inventory (preprod/production in scout-inventory). Sites left
  unpinned follow `/en/latest`, which can show docs for unreleased features.

## Dry Run Mode

Before releasing, you can preview what the changelog will look like:

1. Trigger the Release workflow with **dry_run** checked
2. The workflow generates and displays the changelog
3. No commits, tags, or releases are created
4. Review the output in the workflow logs

This is useful for verifying the changelog looks correct before committing to a release.

## Releasing from a Non-Main Branch

The usual release procedure runs the workflow from `main`. However, if you need to create a release from a different branch (e.g., a hotfix branch for a patch release), the workflow supports this.

### Steps

1. Go to **GitHub Actions** → **Release** workflow
2. In the **"Use workflow from"** dropdown, select the branch you want to release from
3. Check **skip_dev_reset** (this prevents an unnecessary commit resetting versions back to dev, and avoids an unneeded CI run on a branch that doesn't need dev versions)
4. Enter the version and click **"Run workflow"**

The end result is the same as a normal release: a tagged `vX.Y.Z` commit on the branch, with a GitHub release and changelog.

### Branch Name Requirements

The release workflow pushes a version bump commit to the branch and then waits for the CI workflow to build it. The CI workflow only runs on pushes to branches matching specific patterns: `main`, `ci-**`, and `demo**`. If you run the release from a branch that doesn't match any of these patterns, the workflow will time out waiting for a CI run that never starts.

To release from a non-main branch, ensure the branch name starts with `ci-` (e.g., `ci-hotfix-3.0.1`).

### Image Publishing

The CI workflow's `publish` job only runs on `main`. For non-main releases, the release workflow handles image publishing directly: after the CI build succeeds, it downloads the built image artifacts from the CI run and pushes them to GHCR with the release version tag (e.g., `3.0.1`). This ensures versioned images are available regardless of which branch the release is made from, without affecting the `latest` tag.

### Why Skip the Dev Reset?

The `skip_dev_reset` option prevents the workflow from committing dev versions back to the branch after the release. This is recommended for non-main releases because it avoids an extra commit and CI run on a branch that doesn't need dev versions.

Skipping the reset is a good practice for non-main releases but not strictly required.

## Failure and Recovery

Because the tag is created at the end of the workflow (after everything else succeeds), recovery from failures is straightforward.

### Workflow Fails Before Version Bump
- Nothing has changed
- **Recovery**: Fix the issue, re-run the workflow

### Build Fails Due to a Bug
- Version bump commit exists and is still the head of `main`; the build failed
- Reset to dev versions has **not** happened — `reset-dev` only runs on a successful release — so `main` is stamped at `X.Y.Z` while nothing is published at that version. Everything downstream feels it: `derive-version` reads `X.Y.Z`, `check-image-exists` finds no such tag, and every PR rebuilds and rescans all eight images until this is undone.
- **Recovery**, in this order:
  1. Land the fix on `main` as a normal PR.
  2. Land a commit whose message contains a line reading exactly `Reset to dev versions`. This is load-bearing: `validate` greps for it over `bump..HEAD`, and `version-bump` re-stamps only when it finds one. Without it, a re-dispatch pins straight back to the failed stamp commit and fails identically. `main` is PR-gated and only the release App bypasses that, so the line has to survive the squash — put it in the squash body: `gh pr merge <N> --squash --body "Reset to dev versions"`. Verify with `git log --grep="^Reset to dev versions$" <bump-sha>..origin/main` before continuing.
  3. Re-dispatch the same version: `gh workflow run release.yaml --ref main -f version=X.Y.Z`. `validate` tolerates the orphan boundary tag, `version-bump` re-stamps the fixed HEAD, and the tag moves forward to the commit that was actually built.

### Release Creation Fails (Rare)
- Version bump commit exists on `main`, build succeeded, but `gh release create` failed
- Reset has **not** happened (reset only runs when release succeeds or is skipped, not when it fails)
- **Recovery**: Re-run the workflow. It will:
  - Skip version bump (reuses the existing commit)
  - Find the existing successful build
  - Retry release creation
  - Reset to dev versions

### Workflow Fails After Release, Before Reset
- Release and tag exist and are valid
- `main` still has release versions instead of dev versions
- **Recovery**: Re-run the workflow. It detects the release exists and skips to the reset step.

### Idempotent Design

The workflow checks state before each step:
- **Version bump**: Skips if a version bump commit exists AND no reset commit followed it. Creates a new bump if a reset exists (meaning we need to start fresh after a previous build failure).
- **Release**: Skips if the GitHub release already exists.
- **Reset**: Skips if a reset commit already exists after the version bump.

This allows safe re-runs after partial failures without manual intervention.

### Important Notes

- The **tag points to HEAD** at release time, which may be the version bump commit or a later fix commit. This ensures the tag references the exact code that was built and released.

## Reset Timing

`reset-dev` is gated on `needs.release.result == 'success'`, so a failure anywhere earlier leaves `main` stamped at `X.Y.Z`. That is intentional: the version bump commit survives, so a retry can reuse the build that already succeeded, or re-stamp a fixed HEAD once a `Reset to dev versions` commit follows it.

It deliberately does not consult `validate`'s `reset_exists`. That flag is computed before `version-bump` runs, so on a re-release it reports the *previous* cycle's reset and skips the current one — which is how v4.1.0's re-release left `main` stamped at 4.1.0.

While `main` is stamped, nothing is published at that version, so `check-image-exists` reports every image absent and every unrelated PR rebuilds and rescans all eight. `verify-dev-reset` is gated on the same successful release, so it does not fire here — the red release run is the only signal. Recovery is in [Build Fails Due to a Bug](#build-fails-due-to-a-bug).

## CI Components

### 1. Build Workflow (Existing, Unchanged)

**File**: Existing build workflows

**Triggers**: Push to `main`, `ci-**`, or `demo**` branches

**Behavior**: Builds and tests artifacts. Tags are derived from version files:
- Dev versions (`latest`, `0.0.0-dev`, etc.) → publishes with `latest` tag (main only)
- Release versions (`2.1.0`) → publishes with `2.1.0` tag (main only)

The CI workflow's `publish` job only runs on `main`. For non-main branches, the release workflow handles image publishing directly (see below).

### 2. Release Workflow (New)

**File**: `.github/workflows/release.yaml`

**Triggers**: `workflow_dispatch` (manual)

**Inputs**:
| Input | Description | Required |
|-------|-------------|----------|
| `version` | Version to release (e.g., `2.1.0`) | Yes |
| `dry_run` | Preview changelog without releasing | No (default: false) |
| `skip_dev_reset` | Skip resetting versions back to dev after release (use for releases from non-main branches) | No (default: false) |

**Responsibilities**:
1. Validate version format and check tag doesn't exist
2. Update version files and commit
3. Wait for Build Workflow to complete
4. Publish Docker images to GHCR (non-main branches only)
5. Create GitHub release with auto-generated changelog
6. Create version tag
7. Reset to dev versions and commit

### 3. Version Update Script

**File**: `.github/scripts/update-versions.sh`

Updates all version files. Supports two modes:

```bash
# Set release version
.github/scripts/update-versions.sh 2.1.0

# Reset to dev versions
.github/scripts/update-versions.sh dev
```

## GitHub App Setup

GitHub Actions workflows that need to push commits or create pull requests on protected branches use GitHub Apps for authentication. Apps provide bot identities with scoped permissions that aren't tied to personal accounts.

### Why GitHub Apps?

- **Branch protection bypass**: `GITHUB_TOKEN` cannot push to protected branches. A GitHub App can be added as an allowed actor in branch protection rules.
- **Pull request creation**: `GITHUB_TOKEN` cannot create PRs unless the repo-wide "Allow GitHub Actions to create and approve pull requests" setting is enabled. This setting affects all workflows, so using an App is more targeted.
- **Bot identity**: Commits appear as authored by `<app-name>[bot]` rather than a personal account.

### Current Apps

| App | Secrets | Purpose | Permissions | Branch protection bypass |
|-----|---------|---------|-------------|--------------------------|
| `scout-release` | `RELEASE_APP_ID`, `RELEASE_APP_PRIVATE_KEY` | Release workflow: pushes version bump/reset commits directly to `main` | Contents: Read and write | Yes |
| `scout-copyright` | `COPYRIGHT_APP_ID`, `COPYRIGHT_APP_PRIVATE_KEY` | Copyright year workflow: pushes a feature branch and creates a PR | Contents: Read and write, Pull requests: Read and write | No |

### Creating a GitHub App

1. Go to **Settings** → **Developer settings** → **GitHub Apps** → **New GitHub App**
2. Configure:
   - **Name**: e.g., `scout-release`, `scout-copyright`
   - **Homepage URL**: Repository URL (required but not used)
   - **Webhook**: Uncheck "Active"
   - **Permissions**: Set repository permissions as needed (see table above)
   - **Where can this app be installed?**: Only on this account
3. Click **Create GitHub App**

### Generating Credentials

1. On the app's settings page, note the **App ID** (numeric, not the Client ID)
2. Scroll to **Private keys** → **Generate a private key**
3. A `.pem` file will be downloaded

### Installing the App

1. Go to the app's settings → **Install App**
2. Select your organization
3. Choose **Only select repositories** and select the repos that need it

### Adding Secrets

Secrets can be set at the org level to share across repos:

```bash
gh secret set <APP_ID_SECRET> --org <org> --visibility selected --repos repo1,repo2 --body "<app-id>"
gh secret set <PRIVATE_KEY_SECRET> --org <org> --visibility selected --repos repo1,repo2 < /path/to/private-key.pem
```

Or at the repo level:

```bash
gh secret set <APP_ID_SECRET> --body "<app-id>"
gh secret set <PRIVATE_KEY_SECRET> < /path/to/private-key.pem
```

### Branch Protection (Direct Push Apps Only)

For apps that push directly to `main` (e.g., `scout-release`):

1. Go to **Settings** → **Branches** → **main** → **Edit**
2. Under "Allow specified actors to bypass required pull requests"
3. Add the app

Apps that only create PRs (e.g., `scout-copyright`) do not need this.

### Workflow Authentication

Workflows use `actions/create-github-app-token` to generate a short-lived installation token:

```yaml
- name: Generate token from GitHub App
  id: app_token
  uses: actions/create-github-app-token@v1
  with:
    app-id: ${{ secrets.<APP_ID_SECRET> }}
    private-key: ${{ secrets.<PRIVATE_KEY_SECRET> }}

- uses: actions/checkout@v4
  with:
    token: ${{ steps.app_token.outputs.token }}
```

The checkout `token` ensures `git push` uses the App's credentials. For API calls (e.g., `gh pr create`), set `GH_TOKEN`:

```yaml
env:
  GH_TOKEN: ${{ steps.app_token.outputs.token }}
```

## Version Files Reference

This section documents all files containing version strings. The Release Workflow's version update script handles updating these files. This list is maintained for reference and troubleshooting.

### Ansible Role Defaults (Docker Image Tags)

| File | Variable |
|------|----------|
| `ansible/roles/scout_common/defaults/main.yaml` | `scout_notebook_image_tag` |
| `ansible/roles/extractor/defaults/main.yaml` | `hl7log_extractor_image_tag` |
| `ansible/roles/extractor/defaults/main.yaml` | `hl7_transformer_image_tag` |
| `ansible/roles/launchpad/defaults/main.yaml` | `launchpad_image_tag` |
| `ansible/roles/report_viewer/defaults/main.yaml` | `report_viewer_image_tag` |
| `ansible/roles/hl7-listener/defaults/main.yaml` | `hl7_listener_image_tag` |
| `ansible/roles/app_manager/defaults/main.yaml` | `app_manager_image_tag` |

### Python Packages

| File | Field | Dev Value |
|------|-------|-----------|
| `extractor/hl7-transformer/pyproject.toml` | `version` | `0.0.dev0` |
| `extractor/hl7-transformer/VERSION` | entire file | `latest` |
| `report-viewer/pyproject.toml` | `version` | `0.0.dev0` |
| `report-viewer/VERSION` | entire file | `latest` |
| `app-manager/pyproject.toml` | `version` | `0.0.dev0` |
| `app-manager/VERSION` | entire file | `latest` |

### Java/Gradle Build Files

| File | Field |
|------|-------|
| `extractor/hl7log-extractor/build.gradle` | `version` |
| `hl7-listener/build.gradle` | `version` |
| `keycloak/event-listener/build.gradle` | `version` |
| `tests/ingest/build.gradle` | `version` |

### npm Packages

| File | Field |
|------|-------|
| `launchpad/package.json` | `version` |
| `tests/auth/package.json` | `version` |

**Note**: `package-lock.json` is auto-generated by npm. The Release Workflow runs `npm install` after updating `package.json`.

### Helm Charts

**Scout Application Charts**:

| File | Fields | Dev Values |
|------|--------|------------|
| `helm/launchpad/Chart.yaml` | `version`, `appVersion` | `0.0.0-dev`, `"latest"` |
| `helm/launchpad/values.yaml` | `image.tag` | `latest` |
| `helm/report-viewer/Chart.yaml` | `version`, `appVersion` | `0.0.0-dev`, `"latest"` |
| `helm/report-viewer/values.yaml` | `image.tag` | `latest` |
| `helm/scout-app-manager/Chart.yaml` | `version`, `appVersion` | `0.0.0-dev`, `"latest"` |
| `helm/scout-app-manager/values.yaml` | `image.tag` | `latest` |
| `helm/extractor/hl7-transformer/Chart.yaml` | `version`, `appVersion` | `0.0.0-dev`, `"latest"` |
| `helm/extractor/hl7log-extractor/Chart.yaml` | `version`, `appVersion` | `0.0.0-dev`, `"latest"` |
| `helm/hl7-listener/Chart.yaml` | `version`, `appVersion` | `0.0.0-dev`, `"latest"` |

**Charts for External Applications** (do NOT update `appVersion`):

| File | Field | Dev Value | Note |
|------|-------|-----------|------|
| `helm/hive-metastore/Chart.yaml` | `version` only | `0.0.0-dev` | `appVersion` tracks Hive version |
| `helm/voila/Chart.yaml` | `version` only | `0.0.0-dev` | `appVersion` tracks Voila version |
| `helm/open-webui-bootstrap/Chart.yaml` | `version` only | `0.0.0-dev` | `appVersion` is unused — chart orchestrates a Job against the runtime-discovered OWUI image |
| `helm/voila/values.yaml` | `image.tag` | `latest` | Uses scout-notebook image (shared with JupyterHub singleuser) |
| `helm/scout-dashboards/Chart.yaml` | `version` only | `0.0.0-dev` | `appVersion` is unused — chart orchestrates Superset asset imports |
| `helm/keycloak-config-cli/Chart.yaml` | `version` only | `0.0.0-dev` | `appVersion` tracks the keycloak-config-cli version |
| `helm/scout-opa/Chart.yaml` | `version` only | `0.0.0-dev` | `appVersion` is unused — chart deploys the upstream `openpolicyagent/opa` image, tagged from `values.yaml` |
| `helm/temporal-bootstrap/Chart.yaml` | `version` only | `0.0.0-dev` | `appVersion` is unused — chart runs Helm-hook Jobs against `temporalio/admin-tools` |

### VERSION Files

| File | Dev Value |
|------|-----------|
| `extractor/hl7-transformer/VERSION` | `latest` |
| `report-viewer/VERSION` | `latest` |
| `app-manager/VERSION` | `latest` |
| `helm/scout-notebook/VERSION` | `latest` |

## Files NOT to Update

These files track external dependency versions and should NOT be updated as part of a Scout release:

| File | Purpose |
|------|---------|
| `launchpad/package-lock.json` | Auto-generated by npm |
| `ansible/group_vars/all/versions.yaml` | External dependency versions |
| `helm/superset/VERSION` | Apache Superset application version |
| `keycloak/VERSION` | Keycloak application version |
| `helm/dcm4chee/Chart.yaml` | Optional external component |
| `helm/orthanc/Chart.yaml` | Optional external component |

## CI Version Detection (Current)

The current GitHub Actions workflow uses `.github/actions/derive-version/action.yaml` to detect versions from source files. Priority order:

1. `VERSION` file (if present)
2. `package.json` version field
3. `build.gradle` version field
4. `pyproject.toml` version field

This will continue to be used by the Build Workflow to determine artifact versions.
