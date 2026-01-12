# Scout Versioning and Releases

This document describes Scout's versioning strategy and the automated release workflow.

## Versioning Strategy

Scout uses a **tag-triggered release workflow** with release candidates (RCs). The workflow:

1. **Source files maintain dev versions** - no manual version bumps for day-to-day development
2. **RC tags trigger pre-releases** - with auto-generated changelogs
3. **Human approval required** - reviewer can edit changelog before finalizing
4. **Version bump commits created at release time** - the repo contains a commit with release versions
5. **Automatic revert after release** - dev versions restored automatically

### Key Points

- Git tags are the source of truth for release versions
- Pre-releases use dev artifacts (tagged `latest`)
- Final releases use properly versioned artifacts (tagged `X.Y.Z`)
- The final `vX.Y.Z` tag points to the version bump commit, not the original RC commit

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
    |===== PHASE 1: RC Creation =====                       |
    |                           |                           |
    |-- git tag v2.1.0-rc1 ---->|                           |
    |-- git push --tags ------->|                           |
    |                           |                           |
    |                           |-- RC Workflow triggers -->|
    |                           |                           |
    |                           |     Generate changelog    |
    |                           |     Create pre-release    |
    |                           |     (attach dev artifacts |
    |                           |      if already built)    |
    |                           |                           |
    |                           |-- Build Workflow -------->|
    |                           |   (if not already run)    |
    |                           |                           |
    |                           |     Build dev artifacts   |
    |                           |     Publish with 'latest' |
    |                           |     Attach to pre-release |
    |                           |                           |
    |<-- Pre-release ready -----|                           |
    |                           |                           |
    |===== PHASE 2: Human Review =====                      |
    |                           |                           |
    |-- Review pre-release ---->|                           |
    |-- Edit changelog -------->|                           |
    |                           |                           |
    |===== PHASE 3: Finalize Release =====                  |
    |                           |                           |
    |-- Trigger Finalize ------>|                           |
    |   (workflow_dispatch)     |                           |
    |                           |-- Finalize Workflow ----->|
    |                           |                           |
    |                           |     Version bump commit   |
    |                           |     (X.Y.Z in all files)  |
    |                           |          |                |
    |                           |          v                |
    |                           |     Build Workflow runs   |
    |                           |     (triggered by commit) |
    |                           |          |                |
    |                           |          v                |
    |                           |     Publish X.Y.Z artifacts
    |                           |          |                |
    |                           |          v                |
    |                           |     Create full release   |
    |                           |     Tag commit as vX.Y.Z  |
    |                           |          |                |
    |                           |          v                |
    |                           |     Revert commit         |
    |                           |     (back to dev versions)|
    |                           |                           |
    |<-- Release complete ------|                           |
```

### Phase 1: RC Creation

1. **Tag an RC** on the commit you want to release:
   ```bash
   git tag v2.1.0-rc1
   git push origin v2.1.0-rc1
   ```

2. **RC Workflow triggers** and:
   - Parses the version from the tag (`v2.1.0-rc1` → `2.1.0`)
   - Generates changelog from commits since the last release
   - Creates a GitHub pre-release with the changelog
   - Attaches dev artifacts if they're already built (see Race Condition Handling)

3. **Build Workflow** (triggered by the commit, may run before or after RC Workflow):
   - Builds and publishes artifacts with `latest` tag
   - Checks if a pre-release exists for this commit
   - If so, attaches artifact links to the pre-release

**Race Condition Handling**: The RC tag and Build workflows may run in either order. Both check for each other:
- RC Workflow: looks for existing artifacts → attaches if found
- Build Workflow: looks for existing pre-release → attaches artifacts if found
- Whichever runs second completes the attachment

### Phase 2: Human Review

1. **Review the pre-release** in GitHub Releases
2. **Edit the changelog** if needed (add context, fix formatting, highlight breaking changes)
3. **Verify artifacts** are attached and builds succeeded

### Phase 3: Finalize Release

1. **Trigger the Finalize Workflow** via `workflow_dispatch` in GitHub Actions:
   - Input `version`: `2.1.0`
   - Input `rc_tag`: `v2.1.0-rc1`

2. **Finalize Workflow** executes:
   - Creates version bump commit (all version files updated to `2.1.0`)
   - Waits for Build Workflow to complete (triggered by the commit)
   - Creates full release with changelog copied from pre-release
   - Tags the version bump commit as `v2.1.0`
   - Creates revert commit (all version files back to dev versions)

3. **Result**:
   - Pre-release (`v2.1.0-rc1`) remains as historical record
   - Full release (`v2.1.0`) is published
   - Docker images tagged `2.1.0` are available
   - `main` branch is back to dev versions

## Triggering a Release

### Via GitHub Actions UI

1. Go to **Actions** → **Finalize Release** workflow
2. Click **Run workflow**
3. Fill in:
   - `version`: The version to release (e.g., `2.1.0`)
   - `rc_tag`: The RC tag to promote (e.g., `v2.1.0-rc1`)
4. Click **Run workflow**

### Tag Patterns

| Tag Pattern | CI Action |
|-------------|-----------|
| `vX.Y.Z-rcN` (e.g., `v2.1.0-rc1`) | RC Workflow: creates pre-release |
| `vX.Y.Z` (e.g., `v2.1.0`) | **Ignored** (created by CI, not manually) |
| Push to `main` | Build Workflow: builds and publishes `latest` |

## Failure and Recovery

### RC Workflow Fails
- Pre-release not created (or partial)
- **Recovery**: Fix the issue, push a new RC tag (`v2.1.0-rc2`)

### Build Workflow Fails (during RC phase)
- Pre-release exists but no artifacts attached
- **Recovery**: Fix build issue; artifacts will be attached when build succeeds. Or push new RC.

### Finalize Workflow Fails (during version bump)
- Version bump commit may or may not exist on `main`
- **Recovery**: Check state, fix issue, re-run Finalize Workflow. May need to manually revert partial changes.

### Finalize Workflow Fails (after version bump, before release)
- Version bump commit exists, artifacts may be published
- **Recovery**: Re-run Finalize to complete release creation and revert

### Finalize Workflow Fails (after release, before revert)
- Release exists and is valid
- **Recovery**: Manually create revert commit, or re-run Finalize (should detect release exists and skip to revert)

### General Recovery

The Finalize Workflow is designed to be idempotent where possible:
- Checks if version bump commit exists → skips if so
- Checks if release exists → skips if so
- Checks if revert commit exists → skips if so

This allows safe re-runs after partial failures.

## CI Components

> **Note**: The CI workflows described below are planned but not yet implemented.

### 1. Build Workflow (Existing, Minor Modification)

**File**: Existing build workflows

**Triggers**: Push to `main`

**Current behavior**: Builds and publishes artifacts with `latest` tag

**New behavior**: After publishing, check if a pre-release exists for this commit. If so, attach artifact links to the pre-release.

### 2. RC Workflow (New)

**File**: `.github/workflows/rc.yaml`

**Triggers**: Push of tag matching `v*.*.*-rc*`

**Responsibilities**:
1. Parse version from tag
2. Generate changelog from commits since last release
3. Create GitHub pre-release
4. Attach dev artifacts if already built

### 3. Finalize Workflow (New)

**File**: `.github/workflows/finalize-release.yaml`

**Triggers**: `workflow_dispatch` (manual)

**Inputs**:
| Input | Description | Required |
|-------|-------------|----------|
| `version` | Version to release (e.g., `2.1.0`) | Yes |
| `rc_tag` | RC tag to promote (e.g., `v2.1.0-rc1`) | Yes |

**Responsibilities**:
1. Version bump commit
2. Wait for Build Workflow
3. Create full release (copy changelog from pre-release)
4. Tag version bump commit
5. Revert commit

### 4. Version Update Script

**File**: `.github/scripts/update-versions.sh`

Updates all version files to either release version or dev versions.

### 5. Version Parser

Extracts clean version from RC tag:
```
v2.1.0-rc1  →  VERSION=2.1.0, RC_NUMBER=1
v2.1.0-rc12 →  VERSION=2.1.0, RC_NUMBER=12
```

## Version Files Reference

This section documents all files containing version strings. With the automated release workflow, the Finalize Workflow handles updating these files. This list is maintained for reference and troubleshooting.

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
