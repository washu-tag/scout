# Scout Versioning and Releases

This document describes Scout's versioning strategy and the automated release workflow.

## Versioning Strategy

Scout uses a **manual dispatch release workflow**. The workflow:

1. **Source files maintain dev versions** - no manual version bumps for day-to-day development
2. **Human triggers release via GitHub Actions** - specifying the version to release
3. **Version bump commit created at release time** - the repo contains a commit with release versions
4. **Automatic revert after release** - dev versions restored via `git revert`
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
    |                           |     Wait for build        |
    |                           |          |                |
    |                           |          v                |
    |                           |     Create release        |
    |                           |     (auto-gen changelog)  |
    |                           |     Create vX.Y.Z tag     |
    |                           |          |                |
    |                           |          v                |
    |                           |     git revert HEAD       |
    |                           |     Push to main          |
    |                           |                           |
    |<-- Release complete ------|                           |
```

### Triggering a Release

1. **Go to GitHub Actions** → **Release** workflow
2. **Click "Run workflow"**
3. **Enter the version** (e.g., `2.1.0`)
4. Optionally check **dry_run** to preview the changelog without releasing
5. **Click "Run workflow"**

### What the Workflow Does

1. **Validates** the version format and checks the tag doesn't already exist
2. **Searches git history** for an existing version bump commit (for idempotent re-runs)
3. **Generates changelog preview** from PRs since the last release
4. **Updates version files** and commits the version bump to `main` (if not already done)
5. **Waits for the Build Workflow** to complete on HEAD (builds versioned artifacts)
6. **Creates the GitHub release** with auto-generated changelog
7. **Creates the `vX.Y.Z` tag** pointing at HEAD (the commit that was built)
8. **Reverts the version bump commit** via `git revert` to restore dev versions

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

### Workflow Fails After Version Bump, Before Release
- Version bump commit exists on `main`, but no tag or release
- **Recovery**: Re-run the workflow. It searches git history for the version bump commit and skips to waiting for the build.

### Build Fails Due to a Bug
- Version bump commit exists, but build failed
- **Recovery**: Push fix commits to `main`. Once the build passes, re-run the release workflow. It will:
  - Find the existing version bump commit in git history
  - Skip creating a new version bump
  - Wait for the build on HEAD to succeed
  - Create the release and tag pointing to HEAD (which includes your fixes)
  - Revert the version bump commit (not HEAD) to restore dev versions

### Workflow Fails After Release, Before Revert
- Release and tag exist and are valid
- `main` still has release versions instead of dev versions
- **Recovery**: Re-run the workflow. It detects the release exists and skips to the revert step.

### Idempotent Design

The workflow checks state before each step:
- Searches git history for version bump commit → skips version bump if found
- Checks if release exists → skips release creation if so
- Checks if revert of the version bump exists → skips revert if so

This allows safe re-runs after partial failures without manual intervention.

### Important Notes

- The **tag points to HEAD** at release time, which may be the version bump commit or a later fix commit. This ensures the tag references the exact code that was built and released.
- The **revert targets the version bump commit**, not HEAD. This correctly restores dev versions even if fix commits were added after the version bump.

## CI Components

> **Note**: The CI workflows described below are planned but not yet implemented.

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
2. Generate changelog from PRs since last release
3. Update version files and commit
4. Wait for Build Workflow to complete
5. Create GitHub release with changelog
6. Create version tag
7. Revert version bump commit

### 3. Version Update Script

**File**: `.github/scripts/update-versions.sh`

Updates all version files to the specified release version. Called with:
```bash
.github/scripts/update-versions.sh 2.1.0
```

The revert to dev versions is handled by `git revert`, not by this script.

## GitHub App Setup

The Release Workflow needs to push commits to `main`, which is a protected branch. A GitHub App provides a bot identity that can bypass branch protection without being tied to a personal account.

### Creating the GitHub App

1. Go to **Settings** → **Developer settings** → **GitHub Apps** → **New GitHub App**
2. Configure:
   - **Name**: `scout-release` (or similar)
   - **Homepage URL**: Repository URL (required but not used)
   - **Webhook**: Uncheck "Active"
   - **Permissions**:
     - Repository → Contents: **Read and write**
     - Repository → Metadata: **Read-only** (auto-selected)
   - **Where can this app be installed?**: Only on this account
3. Click **Create GitHub App**

### Generating Credentials

1. On the app's settings page, note the **App ID**
2. Scroll to **Private keys** → **Generate a private key**
3. Download the `.pem` file

### Installing the App

1. Go to the app's settings → **Install App**
2. Select your repository
3. Note the **Installation ID** from the URL (`/installations/<ID>`)

### Repository Configuration

1. Add repository secrets:
   - `RELEASE_APP_ID`: The App ID
   - `RELEASE_APP_PRIVATE_KEY`: Contents of the `.pem` file

2. Update branch protection for `main`:
   - Go to **Settings** → **Branches** → **main** → **Edit**
   - Under "Allow specified actors to bypass required pull requests"
   - Add the `scout-release` app

### Workflow Authentication

The Release Workflow uses the GitHub App to authenticate:

```yaml
- name: Generate token from GitHub App
  id: app_token
  uses: actions/create-github-app-token@v1
  with:
    app-id: ${{ secrets.RELEASE_APP_ID }}
    private-key: ${{ secrets.RELEASE_APP_PRIVATE_KEY }}

- uses: actions/checkout@v4
  with:
    fetch-depth: 0
    token: ${{ steps.app_token.outputs.token }}
```

Commits will appear as authored by `scout-release[bot]`.

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
| `tests/build.gradle` | `version` |

### npm Packages

| File | Field |
|------|-------|
| `launchpad/package.json` | `version` |

**Note**: `package-lock.json` is auto-generated by npm. The Finalize Workflow should run `npm install` after updating `package.json`.

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
