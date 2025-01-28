# Pre-commit overview

This project uses [pre-commit](https://pre-commit.com/) to maintain a tidy and consistent repository. When getting started,
you can install pre-commit on a Mac with homebrew:

```bash
$ brew install pre-commit
```

Once `pre-commit` has been installed, the git commit hooks for the repository can be installed from running the following
from within the directory corresponding to your local copy of this repository:

```bash
$ pre-commit install
```

With the git commit hooks installed, `pre-commit` will automatically run the configured hooks on all changed files when you make a commit
locally. If you wish to run the commit hooks against all files to test things out, you can do so with:

```bash
$ pre-commit run --all-files
```

# Pre-commit configuration

The configuration for the pre-commit tool is stored in [.pre-commit-config.yaml](.pre-commit-config.yaml). Currently, the checks are:

## Default hooks

A few out-of-the-box hooks from [pre-commit-hooks](https://github.com/pre-commit/pre-commit-hooks) are enabled:
1. check-added-large-files
1. check-merge-conflict
1. detect-private-key

## Prettier (Javascript, Typescript, CSS, JSON, YAML)

From the hook defined in [mirrors-prettier](https://github.com/pre-commit/mirrors-prettier), *Javascript*, *Typescript*,
*CSS*, *JSON*, and *YAML* files are reformatted using [Prettier](https://prettier.io/). The configuration used for prettier is defined
in [.prettierrc.json](.prettierrc.json). This code formatting will apply automatically to any microservices or other projects within the monorepo.

## ESLint (Javascript, Typescript)

From the hook defined in [mirrors-eslint](https://github.com/pre-commit/mirrors-eslint), *Javascript* and *Typescript* files are linted using
[ESLint](https://eslint.org/). The configuration used for ESLint is defined in [eslint.config.cjs](eslint.config.cjs). This code
formatting will apply automatically to any microservices or other projects within the monorepo.

## Black (Python)

From the hook defined in [black-pre-commit-mirror]([https://github.com/pre-commit/mirrors-eslint](https://github.com/psf/black-pre-commit-mirror),
*Python* files are linted using [Black](https://black.readthedocs.io/en/stable/). There is no configuration applied to Black, so their default
settings will apply. This code formatting will apply automatically to any microservices or other projects within the monorepo.

## Checkstyle (Java)

Unfortunately, the configuration for Java code formatting and linting is less centralized. There is currently only one Java project
in the repository, which uses a gradle plugin to run [checkstyle](https://checkstyle.sourceforge.io/). The configuration for checkstyle
is the standard "google_checks.xml" extracted from the checkstyle library. As of this moment, adding other Java projects would require adding
the same checkstyle plugin and gradle task configuration to the new projects, as well as adding another hook in the pre-commit [config](.pre-commit-config.yaml)
to enable pre-commit formatting/linting on it. Additionally, unlike the previous hooks which run against only the changed files in a commit,
this hook will run against the entire project.

