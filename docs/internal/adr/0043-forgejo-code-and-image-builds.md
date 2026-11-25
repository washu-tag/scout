# ADR 0043: Forgejo for Code and Image Builds

**Date:** 2026-11
**Status:** Proposed. Work in progress: this records the decisions made so far in a
spike, and none of them is final.
**Decision Owner:** TAG Team

## Context

Users want to turn their own code into container images inside Scout, for example
an XNAT Container Service command or an analysis environment, with Claude helping
from a notebook. Scout had no git host, no registry for user images and no CI.
Notebooks currently don't run Docker, and air-gapped sites can't reach GitHub or Docker Hub.

Requirements:

1. Works on on-prem and air-gapped sites, not only on AWS-mode sites (ADR 0035).
2. Users sign in with Scout. No separate accounts or personal access tokens.
3. A user, and Claude working in their notebook, can push code and read build status
   and logs.
4. Builds need no privileged containers, and notebooks get no Docker.
5. Builds get packages only through Scout's package proxies (ADR 0017).
6. XNAT Container Service can run the images that come out.
7. Ships as its own charts, installed as a Pluggable App.

## Decision

### Forgejo as a Pluggable App

- `helm/forgejo` wraps the upstream forgejo-helm chart, and `helm/forgejo-runner`
  runs the build runners. Both install as Pluggable Apps.
- Forgejo answers at `git.<domain>`, because its container registry needs the root
  of a host.
- Packages and images are stored in a MinIO bucket, not on Forgejo's volume.
- Forgejo's Keycloak client ships as a realm fragment (ADR 0037).

### Auth

- **Web UI.** Behind Scout's oauth2-proxy like every other service (ADR 0003). The
  login page redirects straight to Scout sign-in, and Forgejo's own password login
  is off. A Forgejo user is created on first sign-in, for members of `forgejo-user`.
  Members of `forgejo-admin` are Forgejo admins.
- **Git and read-only API with a Scout token.** A request carrying a Scout bearer
  token goes to a git-gate sidecar, an oauth2-proxy that validates the token and
  names the user in a header. Forgejo trusts that header (reverse-proxy auth). The
  route covers git, `GET` on the API and build-log downloads. Writes through the API
  are refused. Forgejo trusts the header from any source, so every other route
  strips it.
- **Machine clients.** The registry, runners and package uploads have a route
  without oauth2-proxy, and Forgejo authenticates them itself.
- **Image push.** Forgejo's built-in Actions token can't push packages, an upstream
  limit still present in the newest release. A loop gives every user a personal
  `REGISTRY_TOKEN` Actions secret instead. Token rotation isn't handled yet.
- **Publishing to XNAT** uses the user's own XNAT token, so XNAT's permission checks
  apply.
- **Claude** uses the REST API directly with the user's token. There is no Scout
  Claude skill for builds. Claude may use the token, but it must not end up in
  transcripts.

### Builds

- **Runners.** Each job runs in a fresh pod with rootless BuildKit, with no
  privileged containers. A NetworkPolicy limits egress to DNS, Traefik and the
  listed proxy addresses. No Docker-in-Docker in Jupyter.
- **One shared workflow** in a public `scout/ci` repo that each user repo calls.
  Users can also write their own workflows. It builds the repo's root Dockerfile,
  or uses repo2docker when there is none.
- **Tags.** A push to `main` builds `:main`, and a `v*` tag builds `:vX`.
- **Packages** come only through the Nexus proxies, passed to builds as build args.
  That includes new Ubuntu apt proxies. Nothing is added to the Squid allowlist for
  builds. A user's own Dockerfile has to opt in to the proxies. R and Julia are out
  of scope.
- **Layer cache** travels inline in the pushed image, and builds import it from the
  image and from `:main`. Exporting the cache to the registry raced the image push
  inside Forgejo's registry: two concurrent uploads of one blob hit a unique-key
  error and a panic.
- **XNAT commands** go in the image's `org.nrg.commands` label.
- **Bring your own image.** Public images come through Harbor's pull-through
  (ADR 0002). Private ones are pushed to Forgejo.
- **Visibility.** Any signed-in user can pull any image, as Forgejo does by default.

### Code from GitHub

- **One-time imports, from GitHub only.** `ALLOWED_DOMAINS` lists the GitHub hosts,
  and `ALLOW_LOCALNETWORKS` stays off, so an import can't reach in-cluster or
  internal addresses. An imported repo is an ordinary repo, and its builds run.
- **No mirrors.** A pull mirror comes with Actions off, so it can't build. With
  mirrors on, the API still created push mirrors even with `DISABLE_NEW_PUSH` set.
  With mirrors off, Forgejo refuses push mirrors.
- **Nothing is pushed out.** Push mirrors and webhooks are off, and runners can't
  reach GitHub.
- **The import page** shows only the Git and GitHub sources. A CSS rule in Forgejo's
  header template hides the rest. It only changes the page; the allowlist does the
  blocking.

### Launchpad

Forgejo opens framed beside the rail with deep links (ADR 0042), from a chip with
the official Git icon.

## Consequences

- Forgejo is GPLv3 and the forgejo-helm chart is MIT. Scout ships Forgejo's images
  unmodified, so obligations arise only when a bundle is redistributed (ADR 0033),
  as with MinIO's AGPL.
- The per-user token loop is a workaround for an upstream gap. It runs every minute
  and holds a push token for every user.
- Images are readable by every Scout user, so nothing private belongs in them yet.
- Nothing cleans up old images and packages, so the MinIO bucket only grows.
- The inline cache covers only the final build stage, so multi-stage builds rebuild
  their earlier stages.
- Forgejo upgrades have to recheck the header template, the CSS rule and the
  reverse-proxy settings.
- Settings removed from the chart can stay live in `app.ini` on Forgejo's volume,
  and stale keys can stay in Secrets written with `stringData`. A removed setting
  has to be checked on a running site.

## Alternatives Considered

- **GitHub with a cloud registry** (GHCR or ECR). Not reachable from air-gapped
  sites. An AWS-mode site could still use it, since only the push target changes.
- **Docker-in-Docker in notebooks.** Needs privileged containers. We could 
  consider Docker-in-Docker support.
- **A shared org that holds the push token.** Users would have to publish through
  it. The per-user token keeps their own namespaces.
- **Registry cache export.** Raced the image push in Forgejo's registry (see
  Builds).
- **Pull mirrors from GitHub.** No builds, and they opened push mirrors over the API
  (see Code from GitHub).
- **A Claude skill for builds.** Not needed. Claude can use the REST API directly.

## Open Questions

Nothing above is settled. These are the known gaps.

- **Auth review.** Check the decisions against the goal: users and Claude push code,
  read build logs and create the Container Service command easily.
- **External access.** Should `git.<domain>` work as a git remote from outside
  Scout? Options: full access, push only, inside the cluster only.
- **git-gate logging.** What it logs per request, and whether to trim it before
  production.
- **GitHub imports on air-gapped sites.** Proposed: a second Squid port that only
  Forgejo can use, with a proxy password, and Forgejo's `[proxy]` settings pointed
  at it. Not built or tested. Until then, imports should be off by default and
  turned on per site.
- **Egress from Forgejo itself.** The Forgejo pod has no egress NetworkPolicy.
- **Images.** Visibility beyond "any signed-in user", cleanup rules, and more
  flexible tag and branch rules.
- **Docker-in-Docker** as an option.
- **A starter repo** and Scout base images with the proxies preset.
- **The staging CA** in place of `PIP_TRUSTED_HOST`.
- **The registry race** between two different builds pushing the same base layer
  hasn't been tested.
- **XNAT.** Turning on Container Service, a pull secret for private images, a
  NetworkPolicy for its pods, a publish step in the SDK, an end-to-end test, and
  XNAT's own launchpad chip.
- **Notebooks.** `git-credential-scout` in the SDK, opening repos in Scout's VS Code,
  and starting a notebook from a built image.

## Related

- ADR 0002 K3s air-gapped deployment (Harbor pull-through)
- ADR 0003 oauth2-proxy
- ADR 0017 Package proxy (Nexus)
- ADR 0018 Squid
- ADR 0033 Build lane bundling
- ADR 0037 Keycloak realm fragments
- ADR 0042 Launchpad shell
