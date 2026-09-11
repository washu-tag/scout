"""The `$(env:...)` holes in the realm: naming them, finding them, proving they fill.

Nothing here substitutes anything. keycloak-config-cli does the substituting,
inside the apply Job, from that container's own environment. This module is the
side of that contract the reconciler owns:

- **naming**, `env_name` and `placeholder`: the variable a fragment client's
  credential will arrive in, and the token written into the document in place
  of the credential itself. The realm names its credentials rather than
  carrying them, which is what makes a composed realm something anyone can
  read, hash and diff.
- **finding**, `references`: every variable a document asks config-cli to
  resolve.
- **proving**, `unresolved`: which of those have nothing behind them. This is
  the reason the module exists at all. config-cli's StringSubstitutor leaves
  `$(env:superset)` alone when nothing sets `superset`, and Keycloak accepts
  that string as a perfectly good client secret -- a working-looking client
  anyone who can read the realm can authenticate as. The failure is silent,
  permanent, and identical across every site, so it has to be caught before the
  apply rather than noticed afterwards.

Substitution reads the whole document, including the parts a fragment wrote.
`schema` refuses `$(` anywhere in a fragment for that reason: without it a
component could put `$(env:oauth2_proxy)` in its own display name and read a
platform client's credential back out of the realm it is allowed to see.
"""

import re

# config-cli's defaults for `import.var-substitution.prefix` / `.suffix`. The
# `env:` lookup is StringSubstitutor's, and the only one Scout uses.
OPEN = "$("
PLACEHOLDER_RE = re.compile(r"\$\(env:([^)]*)\)")

# Substituted into the apply Job from the reconciler's own domain, because
# every base-realm client URL is written against it. Not a credential, so it
# does not live in the client-secrets Secret and is accounted for separately.
SERVER_HOSTNAME = "server_hostname"

# Prefix for the variable a fragment client's credential arrives in. It keeps
# fragments clear of the keys Scout ships, but not of the ones a site adds:
# `keycloak-client-secrets` is site-editable, so `compose` rejects a fragment
# whose derived name lands on a key that Secret already defines.
FRAGMENT_PREFIX = "fragment_"

_NOT_IDENTIFIER = re.compile(r"[^A-Za-z0-9]")


def references(text: str) -> set[str]:
    """Every variable the document asks config-cli to resolve."""
    return set(PLACEHOLDER_RE.findall(text))


def unresolved(text: str, available: set[str]) -> list[str]:
    return sorted(references(text) - available)


def env_name(client_id: str) -> str:
    """The variable a fragment client's credential is passed in as.

    A clientId may hold `.` and `-`, which no environment variable may, so this
    is lossy: `a.b` and `a-b` both land on `fragment_a_b`. Two fragments that
    collide are both rejected rather than one of them silently receiving the
    other's credential.
    """
    return FRAGMENT_PREFIX + _NOT_IDENTIFIER.sub("_", client_id).lower()


def placeholder(name: str) -> str:
    return f"$(env:{name})"


__all__ = [
    "FRAGMENT_PREFIX",
    "OPEN",
    "SERVER_HOSTNAME",
    "env_name",
    "placeholder",
    "references",
    "unresolved",
]
