# CI Flux secret overlay (SOPS)

Air-gap-compatible secret provisioning for the `deploy-and-test-flux` ingest-slice
proof, matching ADR 0031 §3's on-prem default: the fixed-name Secrets the `deploy/`
bases reference are committed here **SOPS-encrypted** and decrypted **in-cluster** by
Flux's kustomize-controller with an age key. No external secret backend, so it runs
offline and proves the same path the air-gapped sites use (ESO/AWS-SM can't, it needs
a network route the on-prem clusters don't have).

Contents:
- `gen-secrets.sh` — renders the 18 ingest-slice Secret manifests from
  `../inventory.yaml` (so the CI creds live in one place, not duplicated here).
- `secrets.enc.yaml` — the rendered Secrets, SOPS-encrypted (data values only). **The
  committed artifact.** Not present until the setup below is run.
- `kustomization.yaml` — lists `secrets.enc.yaml`.
- `.gitignore` — keeps the plaintext render (`secrets.yaml`) out of git.

The Secrets cover the `extractor` dependsOn closure: `superuser-secret` + the five
`cnpg-role-*` companions (scout-core), `postgres-secret` / `s3-secret` / `trino-rw-s3`
(scout-extractor), hive metastore write+readonly, MinIO root config, and the five MinIO
tenant-user creds (scout-data). Cassandra/Elasticsearch datastore secrets are
deliberately excluded (operator-minted).

## One-time setup

1. Generate the CI age keypair:
   ```
   age-keygen -o ci-age.key      # prints the public key; keep ci-age.key OUT of git
   ```
2. Put the **public** key in the repo-root `.sops.yaml` (`age:` recipient).
3. Store the **private** key (the `AGE-SECRET-KEY-...` line in `ci-age.key`) as the
   GitHub Actions secret **`SOPS_AGE_KEY`**. The job feeds it to kustomize-controller.

## Regenerate / rotate the encrypted secrets

```
cd .github/ci_resources/flux-secrets
./gen-secrets.sh > secrets.yaml
sops -e secrets.yaml > secrets.enc.yaml   # encrypts data/stringData per .sops.yaml
rm secrets.yaml
git add secrets.enc.yaml
```

Rotating a value is editing `../inventory.yaml`, re-running the above, and committing
the new `secrets.enc.yaml` — a normal PR.
