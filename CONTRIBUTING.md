# Contributing to Scout

Thanks for contributing. Scout is a monorepo, so you don't need to know the whole stack to
contribute, but do check the docs for the part you're touching before you begin.

- READMEs: the [repo README](README.md) for Scout deployment instructions. A few subprojects also
  have their own READMEs; review those if you're working in one.
- User docs: https://washu-scout.readthedocs.io/en/latest/ (source in [docs/source/](docs/source/))
- Developer docs: [docs/internal/](docs/internal/)
- Architecture decisions: [docs/internal/adr/](docs/internal/adr/). Read the relevant ADR before
  changing something it covers.

Contributions are made through pull requests. Get [agreement on the approach](#maintainer-approval)
first if the change is substantial. If you are using AI for development, see
[use of AI](#use-of-ai).

## Maintainer approval

Open an issue and get agreement on the approach before writing a substantial change. Use the issue
templates in [.github/ISSUE_TEMPLATE/](.github/ISSUE_TEMPLATE/). Review the
[PR template](.github/pull_request_template.md) and use it to frame the conversation. Its questions
about authorization, appsec, performance, data correctness, and compatibility are the ones worth
settling before coding anything.

Substantial changes include:

1. **The data model:** the `reports` schema, a derived table, a view, or anything risking data
   correctness
2. **Security and access:** Trino authorization, OPA policy, Keycloak configuration, or an appsec
   concern
3. **Deployment and release:** topology, a new component, default configuration, feature flags, CI
   workflows, release mechanics, published artifacts, or anything that breaks an upgrade
4. **Anything an ADR covers**
5. **Anything large or opinionated:** a big refactor, a lot of lines, a UI or UX decision, or a
   performance risk at scale

### Why is maintainer approval required?

- **Security.** Scout stores patient data. New endpoints, services, and dependencies all add attack
  surface.
- **Performance.** Queries run against millions of reports. Something that's fine in a dev
  environment can be slow or expensive in production.
- **Many deployments.** Scout runs on-prem, fully air-gapped, or in the cloud. Code that assumes
  internet access, a particular registry, or specific hardware breaks somewhere.
- **Maintenance burden.** Anything we add, we deploy, monitor, patch for CVEs, document, and carry
  through an air-gapped release.
- **Review burden.** We are a small team. Agreeing on the approach first beats redesigning in the
  review comments.

## Lint and format

Before your first commit, install the [pre-commit](docs/internal/precommit.md) hooks.

```bash
pre-commit install
```

The hooks format and lint: prettier on JS/TS/CSS/JSON/YAML, eslint on JS/TS, black on Python, and
checkstyle on Java. They also check for large files, merge markers, and committed private keys.
The Java hooks shell out to `gradlew`, so they need a JDK. **No hook runs tests**, so run the tests
for whatever you changed yourself.

## Tests

- **Unit tests** live with their subproject.
- **Integration and end-to-end tests** live under [tests/](tests/), see
  [integration_tests.md](docs/internal/integration_tests.md).

Add to an existing test file when you can. Wire new tests into CI so they actually run, and keep an
eye on how long they take. CI for PRs runs on every push and already takes a while to run.

## Repo-wide gotchas

- **Don't hand-edit version fields.** `Chart.yaml`, `VERSION`, and `pyproject.toml` versions are
  placeholders stamped at publish time.
- **Site-specific config belongs in `inventory.yaml`**, not in role defaults.
- **Dependency bumps** go in `ansible/group_vars/all/versions.yaml` with a `# renovate:` annotation
  (ADR 0015).
- **New images or charts** need their entry in the `changes` job in
  [.github/workflows/ci.yaml](.github/workflows/ci.yaml) *and* the release path (see
  [versions-and-releases.md](docs/internal/versions-and-releases.md)).

## PRs

Fill in [the PR template](.github/pull_request_template.md).

- **Description.** What the change is and why, at the top. Keep it brief and link to any
  relevant issues.
- **Testing.** A model can write the tests, which is not the same as you confirming the change is
  correct. Say what you checked by hand: commands and output, a screenshot, a curl call, row counts
  before and after, etc.
- **Impact.** Answer the questions that apply to your change and delete the sections that do not
  apply.
- **Right-size.** Too big is hard to review, too small is hard to follow. Aim for one complete
  change per PR and stack them when the work is bigger.
- **Use a Conventional Commit title.** The `PR Title Lint` check enforces the format, and release
  automation reads the title for the version bump and changelog.
- **Docs.** Update [docs/source/](docs/source/) when users see a difference and
  [docs/internal/](docs/internal/) when developers do. Write an ADR when the change settles a
  decision worth recording rather than rediscovering.

## Use of AI

We use AI tools to develop Scout. Write code with Claude, Copilot, whatever works for you.

- **You own the change.** You can explain what it does, why it's built that way, and what happens if
  it's wrong.
- **Issues and PR descriptions are yours.** Write them in your own words. You can use a model to
  draft, but please review and edit it before you post.
- **Quoting a model is fine.** If you wish to include context from an interaction with AI in your
  comments, quote it in a block, attribute it, and add your read of it. Keep the quote short and
  don't paste transcripts.
- **AI-assisted review is welcome.** Running a model over someone's PR often turns up things worth
  raising, but the comment that lands on their PR should be yours. Read through what it found, keep
  what's relevant, and post your own understanding of the model's feedback. Quoting the model if
  needed is fine.
- **Keep code comments relevant and concise.** AI likes to leave comments describing what it did or
  did not do, general narration, and overly verbose or trivial notes. Manually review the comments
  in your diff and cut the ones that don't earn their place in the codebase.
