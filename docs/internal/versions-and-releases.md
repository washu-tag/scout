# Scout Versioning and Releases

This document describes Scout's versioning strategy and the automated release workflow.

## Versioning Strategy

Scout has a build lane (`0.YYYYMMDD.<run>`) and a deliberate release lane
(`X.Y.Z`, major version at least 1). Release-please computes a version and
changelog from Conventional Commit PR titles; a maintainer merges its release
PR. `release-dispatch.yaml` creates a lightweight boundary tag and dispatches
`release.yaml` on `main`. Manual dispatch of that same workflow remains available.

The release workflow keeps the version-bump/reset commits required by Ansible.
It waits for the exact stamped build and its published Flux proof, promotes the
verified package digests, attaches a signed release record, and publishes the
GitHub Release last. A boundary tag or an OCI version tag alone does not mean a
release completed. Failed releases can reserve a version number.

The gate requires the producer and consumer workflows on the upstream default
branch and a new published producer receipt. Fork/local-artifact tests exercise
the implementation but cannot satisfy this upstream release gate. See
[ADR 0030](adr/0030-two-lane-versioning-and-artifact-publishing.md) for the
proposed interim promotion contract.

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

The build lane records concrete artifact digests. Legacy derived image tags
(`latest` between releases, `X.Y.Z` while stamped) remain for Ansible compatibility.

**Note on constrained versions**:
- **Helm charts** require SemVer 2 compliant versions. `latest` is not valid; we use `0.0.0-dev`.
- **Python packages** require PEP-440 compliant versions. `latest` is not valid; we use `0.0.dev0`. A separate `VERSION` file containing `latest` controls the Docker image tag.

## Release Process

### Publication order

1. Validate an upstream `main` release and its version. Preserve the existing
   lightweight boundary tag, or record the dispatch commit when no tag exists.
   Refuse an existing draft/public release and direct recovery to
   `promote-release.yaml` before changing version files.
2. Create or reuse `Update to version X.Y.Z`. Its exact SHA, rather than moving
   `main`, is the target of the release build. Keep the stamp/reset scripts until
   the Ansible cutover.
3. Wait up to 90 minutes for that `ci.yaml` producer attempt and the corresponding
   published `deploy-flux.yaml` proof. Validate repository, workflow, commit, run
   and attempt metadata through GitHub's API. The proof must identify the same
   haul-manifest, bundle and config digests and contain successful ingest and
   authentication legs in the SOPS profile. A per-SHA status is advisory and
   cannot authorize promotion.
4. Under the shared promotion lock, check again that no draft/public release has
   appeared, then package and sign the fifteen release-version Helm charts for
   existing Ansible consumers. The producer has already published the seven
   Scout-versioned image aliases. Promotion checks those images against the
   signed haul and records the separately packaged chart digests; their bytes
   are not the charts tested by the Flux proof.
5. Revalidate evidence, verify all three package artifacts by digest and managed
   key, and prepare `scout-release-X.Y.Z.yaml` plus
   `scout-release-X.Y.Z.sigstore.json`. The record binds source, producer attempt,
   consumer attempt, package digests and compatibility outputs. It references
   the Hauler inventory rather than replacing it with another component list.
6. Create a draft GitHub Release, attach and read back the signed record, add
   matching `X.Y.Z` aliases to the exact manifest/bundle/config digests, and check
   each alias. Move the lightweight source tag only from the expected boundary
   to the tested stamped commit, without force. Publish the GitHub Release only
   after these checks. Aliases across repositories are separate operations;
   partial publication is resumable, not atomic.
7. Clear the matching release PR's pending label, reset `main` to dev placeholders
   unless `skip_dev_reset` was selected, and verify the reset. The release record
   and its signature remain durable GitHub Release assets.

The normal release chart/promotion job and recovery job share
`scout-release-promotion` concurrency. The producer and the release's build/proof
wait do not hold this lock. Retain the operational hold on unrelated merges
while `main` is stamped: legacy tags remain mutable, and a digest mismatch stops
promotion rather than silently accepting another build.

### Triggering a release

Merge the release-please PR, or dispatch **Release** (`release.yaml`) on `main`
with `version=X.Y.Z`. `dry_run=true` previews the changelog and makes no release
changes. `skip_dev_reset=true` leaves the successful release stamp in place and
requires a deliberate later reset.

Non-main release publication is rejected before stamping. Such branches do not
have the trusted published producer/consumer path; their CI success cannot be
substituted for this proof. Build-lane tests and development branches remain
available independently.

Before upgrading a site, update its pinned docs URL alongside its Scout release
pin and review the release's upgrade notes. Verification and the pre-upgrade
backup/rollback limits are described in
[Verifying Releases](../source/operate/verifying-releases.md).

## Failure and Recovery

### Before a draft or release exists

If the failure was transient, re-dispatch the same version. The workflow reuses
the stamped commit when no reset followed it, revalidates the producer and proof,
and retries publication. A failed proof, missing receipt or expired evidence
fails closed; a green status or manual tag alias is not a bypass.

If code must change, land the fix and a reset before re-dispatching. The reset
commit must contain a line exactly `Reset to dev versions`; validation uses it
to distinguish a new build from a retry of the old stamp. On a protected branch,
preserve that line in the squash commit body. Then dispatch the same version to
stamp the fixed source. Do not use this route once a draft or public release
already contains promotion evidence for that version.

### A draft, partial promotion or published release exists

Run **Recover Release Promotion** (`promote-release.yaml`) from upstream `main`,
providing these original identities from the release job's recovery-input
summary or the signed release record:

| Input | Meaning |
|---|---|
| `version` | The same `X.Y.Z` |
| `revision` | Exact stamped source SHA |
| `producer_run_id`, `producer_run_attempt` | Successful Post-Commit Tasks attempt |
| `consumer_run_id`, `consumer_run_attempt` | Successful published Flux proof attempt |
| `boundary_sha` | Original lightweight release-boundary commit (`boundaryRevision` in the signed record) |

Recovery calls the same `tooling/release/promote.py promote` implementation. It
rechecks live GitHub evidence and signatures, accepts matching existing assets
and aliases, and completes missing draft work. It does not rebuild, repackage,
stamp, overwrite conflicting content or accept local-artifact proof. A complete
published release with the same record and aliases is a no-op. A published
release missing the signed record, expired workflow artifacts, or a different
existing digest requires investigation; recovery does not invent replacement
evidence. Keep the original producer and proof artifacts through release
closeout (the workflows retain them for 90 days).

Use this recovery workflow rather than re-running `release.yaml`: repackaging a
Helm chart can change its digest and conflict with an already signed release
record. Once a release is published, treat its content as fixed and ship
changed content under a new version.

### Dev reset failed or was skipped

Recovery verifies/completes promotion but intentionally does not reset `main`.
First check that the published release has the expected signed record and that
no newer release is being prepared. Inspect `main` and confirm its version files
still carry **this** release's version. If they already contain dev placeholders,
there is nothing to reset. If they contain a different release, stop.

From a clean worktree based on current `main`, run:

```bash
bash .github/scripts/update-versions.sh dev
npm --prefix launchpad install --package-lock-only
npm --prefix tests/auth install --package-lock-only
git diff
```

Review the diff for version/reset changes only, then submit it through the
normal protected-branch process with `Reset to dev versions` retained in the
commit message. Verify the merged source is back at dev placeholders. Do not
re-dispatch an already published version just to reset it.

## CI Components

| Component | Responsibility |
|---|---|
| `ci.yaml` | Build-lane artifacts, signed package and attempt-specific producer receipt |
| `deploy-flux.yaml` | Exact published config verification, ingest/authentication tests and attempt-specific proof |
| `release-dispatch.yaml` | Release-please boundary tag and main release dispatch |
| `release.yaml` | Stamp, wait, compatibility charts, verified promotion, successful-release dev reset |
| `promote-release.yaml` | Resume promotion with explicit original identities; no rebuild/reset |
| `tooling/release/promote.py` | Shared evidence validation, signing, alias checks and draft-to-public transition |
| `.github/scripts/update-versions.sh` | Ansible-compatible source stamping and dev reset |

When adding a release artifact, keep the producer path map, release chart list,
`tooling/release/promote.py` compatibility lists and version-file tables below in
sync. A successful core proof does not certify optional services, haul restore,
registry relocation or cold-cache disconnected installation.

## GitHub App Setup

GitHub Actions workflows that need to push commits or create pull requests on protected branches use GitHub Apps for authentication. Apps provide bot identities with scoped permissions that aren't tied to personal accounts.

### Why GitHub Apps?

- **Branch protection bypass**: `GITHUB_TOKEN` cannot push to protected branches. A GitHub App can be added as an allowed actor in branch protection rules.
- **Pull request creation**: `GITHUB_TOKEN` cannot create PRs unless the repo-wide "Allow GitHub Actions to create and approve pull requests" setting is enabled. This setting affects all workflows, so using an App is more targeted.
- **Bot identity**: Commits appear as authored by `<app-name>[bot]` rather than a personal account.

### Current Apps

| App | Secrets | Purpose | Permissions | Branch protection bypass |
|-----|---------|---------|-------------|--------------------------|
| `scout-release` | `RELEASE_APP_ID`, `RELEASE_APP_PRIVATE_KEY` | Stamp/reset commits, release tag/assets, matching release-PR label cleanup | Contents: Read and write, Pull requests: Read and write | Yes |
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

The checkout `token` ensures `git push` uses the App's credentials. Promotion uses
`GH_TOKEN` for read-only CI evidence access and `RELEASE_GH_TOKEN` for App-backed
release/tag/asset access, including reads of private drafts, so the App does not
need Actions permissions. Its managed
cosign key is supplied only to the promotion job. For other API calls (e.g., `gh pr create`), set `GH_TOKEN`:

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
| `ansible/roles/keycloak_fragment_reconciler/defaults/main.yaml` | `keycloak_fragment_reconciler_image_tag` |

### Python Packages

| File | Field | Dev Value |
|------|-------|-----------|
| `extractor/hl7-transformer/pyproject.toml` | `version` | `0.0.dev0` |
| `extractor/hl7-transformer/VERSION` | entire file | `latest` |
| `report-viewer/pyproject.toml` | `version` | `0.0.dev0` |
| `report-viewer/VERSION` | entire file | `latest` |
| `keycloak-fragment-reconciler/pyproject.toml` | `version` | `0.0.dev0` |
| `keycloak-fragment-reconciler/VERSION` | entire file | `latest` |

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
| `helm/keycloak-fragment-reconciler/Chart.yaml` | `version`, `appVersion` | `0.0.0-dev`, `"latest"` |
| `helm/keycloak-fragment-reconciler/values.yaml` | `image.tag` | `latest` |
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
