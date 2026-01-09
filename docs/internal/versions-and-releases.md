# Scout Versioning and Releases

This document describes Scout's versioning strategy and the automated release workflow.

## Versioning Strategy

Scout uses a **tag-triggered release workflow** with release candidates (RCs). Source files maintain development versions (`latest`, `0.0.0-dev`, `0.0.dev0`), and CI extracts the release version from git tags at build time.

### Key Principles

1. **Source files always contain dev versions** - no manual version bumps needed
2. **Git tags are the source of truth** for release versions
3. **RC tags trigger builds** - final release tags are created by CI
4. **Human approval required** before promoting RC to full release

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

### Overview

```
Developer                    GitHub                      CI
    |                           |                         |
    |-- git tag v2.1.0-rc1 ---->|                         |
    |-- git push --tags ------->|                         |
    |                           |-- trigger workflow ---->|
    |                           |                         |
    |                           |     Parse: v2.1.0-rc1 → 2.1.0
    |                           |     Build all components with 2.1.0
    |                           |     Push images tagged 2.1.0
    |                           |     Create GitHub pre-release
    |                           |                         |
    |                           |     [Human approval step]
    |                           |                         |
    |                           |     Create tag v2.1.0
    |                           |     Promote to full release
    |                           |                         |
    |<-- notification ----------|                         |
```

### Step-by-Step

1. **Tag an RC**: Create and push a release candidate tag
   ```bash
   git tag v2.1.0-rc1
   git push origin v2.1.0-rc1
   ```

2. **CI builds**: The RC tag triggers the release workflow, which:
   - Parses the version from the tag (`v2.1.0-rc1` → `2.1.0`)
   - Builds all components with version `2.1.0`
   - Pushes Docker images tagged `2.1.0`
   - Creates a GitHub pre-release

3. **Human approval**: A reviewer:
   - Reviews the pre-release and build artifacts
   - Edits release notes if desired
   - Approves the release (method depends on chosen option - see CI Components)

4. **CI finalizes**: After approval, CI:
   - Creates the final `v2.1.0` tag on the same commit
   - Promotes the pre-release to a full release

### Tag Patterns

| Tag Pattern | CI Action |
|-------------|-----------|
| `vX.Y.Z-rcN` (e.g., `v2.1.0-rc1`) | Triggers release workflow |
| `vX.Y.Z` (e.g., `v2.1.0`) | **Ignored** (created by CI, not manually) |
| Push to `main` | Triggers dev build with `latest` tag |

### Version Extraction

For tag `vX.Y.Z-rcN`:
- Strip the `v` prefix and `-rcN` suffix
- Build version = `X.Y.Z` (not `X.Y.Z-rcN`)
- All packages and images use `X.Y.Z`

Examples:
```
v2.1.0-rc1  →  VERSION=2.1.0
v2.1.0-rc12 →  VERSION=2.1.0
v3.0.0-rc1  →  VERSION=3.0.0
```

### Failure and Retry

If an RC build fails:
1. The pre-release remains in failed state (or is not created)
2. No final `vX.Y.Z` tag is created
3. Fix the issue, then tag a new RC: `v2.1.0-rc2`
4. The new RC triggers a fresh build attempt

Multiple RC tags for the same version are fine - only the successful one creates the final release.

## Manual Release (workflow_dispatch)

The release workflow can also be triggered manually for:
- Re-running a failed release without pushing a new tag
- Creating a release from a specific commit or branch
- Testing the release process

**Inputs**:
| Input | Description | Required |
|-------|-------------|----------|
| `version` | Version to release (e.g., `2.1.0`) | Yes |
| `ref` | Git ref to release (commit SHA, branch, or tag) | No (default: main) |
| `create_rc_tag` | Create RC tag before building | No (default: false) |
| `rc_number` | RC number if creating tag | No (default: 1) |

## CI Components

> **Note**: The CI workflows described below are planned but not yet implemented.

### 1. Release Workflow

**File**: `.github/workflows/release.yaml`

**Triggers**:
- `push.tags`: `v*.*.*-rc*` (RC tags only)
- `workflow_dispatch`: Manual trigger

**Responsibilities**:
- Parse version from tag
- Build all components with extracted version
- Push Docker images
- Create GitHub pre-release
- After approval: create final tag, promote release

### 2. Version Parser

**Location**: Inline in workflow or `.github/scripts/parse-version.sh`

**Output**:
- `VERSION`: Clean version (e.g., `2.1.0`)
- `IS_RC`: Boolean
- `RC_NUMBER`: The RC number if applicable

### 3. Build Jobs

Existing build workflows modified to accept a `version` input parameter:
- `build-launchpad.yaml`
- `build-hl7-transformer.yaml`
- `build-hl7log-extractor.yaml`
- `build-pyspark-notebook.yaml`
- `build-keycloak-event-listener.yaml`

### 4. Human Approval Step

> **DECISION REQUIRED**: Choose Option A or Option B before implementing.

#### Option A: Environment Protection Rules

Single workflow with a `finalize-release` job that requires approval:

```yaml
jobs:
  build:
    # ... builds and creates pre-release ...

  finalize-release:
    needs: build
    environment: production  # Requires approval
    steps:
      # ... creates final tag, promotes release ...
```

**Setup**: Repository Settings → Environments → Create "production" → Add required reviewers

**Flow**:
1. RC tag pushed → workflow runs → pre-release created
2. Workflow pauses at `finalize-release` job
3. Reviewer gets GitHub notification, can edit release notes
4. Reviewer clicks "Approve and deploy" in Actions UI
5. `finalize-release` job runs → full release created

**Pros**: Single workflow, built-in notifications, audit trail
**Cons**: Approval UI in Actions tab (not Releases tab)

#### Option B: Separate Finalization Workflow

Two separate workflows:

**Workflow 1: RC Build** (`.github/workflows/rc-build.yaml`)
- Triggered by: `v*.*.*-rc*` tags
- Does: Build, push images, create pre-release, then stops

**Workflow 2: Finalize Release** (`.github/workflows/finalize-release.yaml`)
- Triggered by: `workflow_dispatch` only
- Does: Create final tag, promote pre-release

**Flow**:
1. RC tag pushed → RC Build runs → pre-release created
2. Reviewer edits release notes in Releases UI
3. Reviewer manually triggers Finalize Release workflow
4. Full release created

**Pros**: Edit release notes in familiar UI, clear separation
**Cons**: Two workflows, manual trigger required, no automatic notification

### 5. Release Finalizer

**Triggered by**: Human approval (Option A) or manual dispatch (Option B)

**Responsibilities**:
1. Create `vX.Y.Z` tag on the same commit as the RC tag
2. Update existing GitHub Release:
   - Change tag from `vX.Y.Z-rcN` to `vX.Y.Z`
   - Remove pre-release flag
   - Keep release notes (may have been manually edited)

## Version Files Reference

This section documents all files containing version strings. With the automated release workflow, **you do not need to manually update these files** - CI handles version injection at build time. This list is maintained for reference and troubleshooting.

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

**Note**: `package-lock.json` is auto-generated by npm. Do not edit manually.

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

This will be superseded by the tag-based version extraction in the new release workflow.
