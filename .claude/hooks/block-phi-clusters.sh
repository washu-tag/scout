#!/usr/bin/env bash
# PHI-safety backstop for Scout (see CLAUDE.md "CRITICAL: PHI Safety" section).
#
# Blocks any Bash command that references Scout's PHI-bearing pre-production or
# production clusters. Development/demo clusters (tagdev-*, tagdemo-*) hold only
# synthetic data and are NOT blocked.
#
# This is a defense-in-depth backstop, not a complete guarantee: it pattern-matches
# the known host/inventory naming conventions. It does not catch hosts referenced by
# bare IP, an alias in ~/.ssh/config, or an already-exported KUBECONFIG env var.
#
# PreToolUse hook: reads the tool-call JSON on stdin; emits a "deny" decision when a
# forbidden pattern is found, otherwise stays silent (allowing the normal flow).
set -uo pipefail

input=$(cat)
cmd=$(printf '%s' "$input" | jq -r '.tool_input.command // empty')

# Patterns for PHI environments. Anchored on the "tag<env>" host convention and the
# "<env>.tag." FQDN / "inventory.<env>" forms so dev/demo never match.
#   tagpreprod-*, tagprod-*       e.g. tagpreprod-control-01
#   preprod.tag.* , prod.tag.*    e.g. preprod01.tag.rcif.io
#   inventory.preprod*, inventory.prod*
phi_pattern='tag(preprod|prod)|(preprod|prod)[0-9]*\.tag\.|inventory\.(preprod|prod)'

if printf '%s' "$cmd" | grep -qiE "$phi_pattern"; then
  reason="BLOCKED by Scout PHI-safety hook: this command targets a pre-production or production cluster, which holds real patient data (PHI). Per CLAUDE.md, agents must NOT access these environments directly — not even to read file names, metadata, sizes, or counts. Present the exact command to the human operator to run themselves, and only analyze PHI-free output they paste back. (Development/demo clusters matching tagdev-* / tagdemo-* are permitted.)"
  jq -n --arg r "$reason" \
    '{hookSpecificOutput: {hookEventName: "PreToolUse", permissionDecision: "deny", permissionDecisionReason: $r}}'
fi

exit 0
