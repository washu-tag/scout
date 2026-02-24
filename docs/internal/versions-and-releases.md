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
    |                           |     Push to main          |
    |                           |          |                |
    |                           |          v                |
    |                           |     Build Workflow runs   |
    |                           |     (triggered by commit) |
    |                           |          |                |
    |                           |          v                |
    |                           |     Wait for build -------+---> [Build fails]
    |                           |          |                |           |
    |                           |          v                |           |
    |                           |     Create release        |           |
    |                           |     (auto-gen changelog)  |           |
    |                           |     Create vX.Y.Z tag     |           |
    |                           |          |                |           |
    |                           |          +<---------------+-----------+
    |                           |          v                |
    |                           |     Reset to dev versions |
    |                           |     Push to main          |
    |                           |                           |
    |<-- Release complete ------|                           |
```

> **Note**: The reset to dev versions step runs regardless of whether the build succeeded or failed. The only exception is if the release job itself fails (e.g., `gh release create` errors) — in that case, reset is skipped to allow a simple retry. See [Design Decision: Reset Timing](#design-decision-reset-timing) for rationale.

### Triggering a Release

1. **Go to GitHub Actions** → **Release** workflow
2. **Click "Run workflow"**
3. **Enter the version** (e.g., `2.1.0`)
4. Optionally check **dry_run** to preview the changelog without releasing
5. **Click "Run workflow"**

### What the Workflow Does

1. **Validates** the version format and checks the tag doesn't already exist
2. **Searches git history** for an existing version bump commit (for idempotent re-runs)
3. **Updates version files** and commits the version bump to `main` (if not already done)
4. **Waits for the Build Workflow** to complete on HEAD (builds versioned artifacts)
5. **Creates the GitHub release** with auto-generated changelog (if build succeeded)
6. **Creates the `vX.Y.Z` tag** pointing at HEAD (if build succeeded)
7. **Resets to dev versions** by running the update script and committing (always, regardless of build result)

### Result

- Release `v2.1.0` is published with changelog
- Docker images tagged `2.1.0` are available
- Tag `v2.1.0` points to the commit that was actually built and released
- `main` branch is back to dev versions

## Dry Run Mode

Before releasing, you can preview what the changelog will look like:

1. Trigger the Release workflow with **dry_run** checked
2. The workflow generates and displays the changelog
3. No commits, tags, or releases are created
4. Review the output in the workflow logs

This is useful for verifying the changelog looks correct before committing to a release.

## Failure and Recovery

Because the tag is created at the end of the workflow (after everything else succeeds), recovery from failures is straightforward.

### Workflow Fails Before Version Bump
- Nothing has changed
- **Recovery**: Fix the issue, re-run the workflow

### Build Fails Due to a Bug
- Version bump commit exists, but build failed
- Reset to dev versions has already happened (see [Design Decision: Reset Timing](#design-decision-reset-timing))
- **Recovery**: Push fix commits to `main`, then re-run the release workflow. It will:
  - Create a new version bump commit (since the previous one is no longer at HEAD)
  - Wait for the build on HEAD to succeed
  - Create the release and tag pointing to HEAD (which includes your fixes)
  - Reset to dev versions

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

## Design Decision: Reset Timing

The workflow always resets to dev versions after the version bump, regardless of whether the build and release succeeded. This is a deliberate design choice with trade-offs worth understanding.

### Current Behavior (Always Reset)

After the version bump commit is pushed, the reset to dev versions happens regardless of the build outcome. If the build fails:

1. Version bump commit is on `main`
2. Build fails
3. Reset to dev versions happens anyway
4. `main` is back to dev versions
5. To release, you must re-run the workflow (which creates a new version bump)

**Exception**: If the release job itself fails (not skipped due to build failure, but actually runs and fails), reset does not happen. This allows a simple retry that reuses the existing version bump commit and successful build. This is a rare scenario that would only occur if `gh release create` fails due to a transient error.

**Advantages:**
- The repository stays in a consistent, expected state (dev versions)
- Simpler mental model: dev versions are always the "normal" state on `main`
- No ambiguity about what versions are currently on `main`

**Disadvantages:**
- Re-running the release requires creating a new version bump commit
- If you push fixes to `main`, they won't be built with release versions until you re-run the workflow

### Alternative: Reset Only After Success

An alternative approach would reset to dev versions only after the release succeeds:

1. Version bump commit is on `main`
2. Build fails
3. `main` stays at release versions
4. You push a fix commit
5. Build runs again with release versions
6. Re-run the workflow, which skips to waiting for the build, then releases

**Advantages:**
- Any fix commits are immediately built with release versions
- The moment the build succeeds, you have versioned artifacts ready
- Re-running the workflow just waits for an existing successful build

**Disadvantages:**
- `main` stays at release versions during the "broken" period, which could be confusing
- Multiple commits may have release versions in their files
- If you abandon the release, you must manually reset to dev versions

### Rationale for Current Choice

The current "always reset" behavior was chosen because:
- It keeps the repository in a predictable state
- It avoids the scenario where `main` has release versions for an extended period
- The extra version bump commit on retry is a minor cost for clearer repository state

Both approaches are valid. If the alternative behavior is preferred, the `reset-dev` job condition could be changed to only run when `release.result == 'success'` (removing the `|| needs.release.result == 'skipped'` clause).

## CI Components

### 1. Build Workflow (Existing, Unchanged)

**File**: Existing build workflows

**Triggers**: Push to `main`

**Behavior**: Builds and publishes artifacts. Tags are derived from version files:
- Dev versions (`latest`, `0.0.0-dev`, etc.) → publishes with `latest` tag
- Release versions (`2.1.0`) → publishes with `2.1.0` tag

### 2. Release Workflow (New)

**File**: `.github/workflows/release.yaml`

**Triggers**: `workflow_dispatch` (manual)

**Inputs**:
| Input | Description | Required |
|-------|-------------|----------|
| `version` | Version to release (e.g., `2.1.0`) | Yes |
| `dry_run` | Preview changelog without releasing | No (default: false) |

**Responsibilities**:
1. Validate version format and check tag doesn't exist
2. Update version files and commit
3. Wait for Build Workflow to complete
4. Create GitHub release with auto-generated changelog
5. Create version tag
6. Reset to dev versions and commit

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
| `ansible/roles/scout_common/defaults/main.yaml` | `jupyter_singleuser_image_tag` |
| `ansible/roles/extractor/defaults/main.yaml` | `hl7log_extractor_image_tag` |
| `ansible/roles/extractor/defaults/main.yaml` | `hl7_transformer_image_tag` |
| `ansible/roles/launchpad/defaults/main.yaml` | `launchpad_image_tag` |

### Python Package

| File | Field | Dev Value |
|------|-------|-----------|
| `extractor/hl7-transformer/pyproject.toml` | `version` | `0.0.dev0` |
| `extractor/hl7-transformer/VERSION` | entire file | `latest` |

### Java/Gradle Build Files

| File | Field |
|------|-------|
| `extractor/hl7log-extractor/build.gradle` | `version` |
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
| `helm/extractor/hl7-transformer/Chart.yaml` | `version`, `appVersion` | `0.0.0-dev`, `"latest"` |
| `helm/extractor/hl7log-extractor/Chart.yaml` | `version`, `appVersion` | `0.0.0-dev`, `"latest"` |

**Charts for External Applications** (do NOT update `appVersion`):

| File | Field | Dev Value | Note |
|------|-------|-----------|------|
| `helm/hive-metastore/Chart.yaml` | `version` only | `0.0.0-dev` | `appVersion` tracks Hive version |
| `helm/voila/Chart.yaml` | `version` only | `0.0.0-dev` | `appVersion` tracks Voila version |
| `helm/voila/values.yaml` | `image.tag` | `latest` | Uses pyspark-notebook image |

### VERSION Files

| File | Dev Value |
|------|-----------|
| `extractor/hl7-transformer/VERSION` | `latest` |
| `helm/jupyter/notebook/VERSION` | `latest` |

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
