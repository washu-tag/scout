# Sourced from ~/.bashrc and /etc/profile.d. Codex reads its key from the variable named
# by the provider's env_key; export it from ~/.codex/auth.json so the key lives in one
# file. The site's /etc/codex/requirements.toml overrides the user's config.toml.
_scout_var=$(sed -n 's/^[[:space:]]*env_key[[:space:]]*=[[:space:]]*"\([A-Za-z_][A-Za-z0-9_]*\)".*/\1/p' \
  /etc/codex/requirements.toml "$HOME/.codex/config.toml" 2>/dev/null | head -n 1)
if [ -n "$_scout_var" ] && [ -r "$HOME/.codex/auth.json" ]; then
  _scout_key=$(jq -r '.OPENAI_API_KEY // empty' "$HOME/.codex/auth.json" 2>/dev/null)
  if [ -n "$_scout_key" ] && [ "$_scout_key" != REPLACE_WITH_YOUR_GATEWAY_KEY ]; then
    export "$_scout_var=$_scout_key"
  fi
fi
unset _scout_var _scout_key

case $- in
  *i*)
    if [ -z "$_scout_key_reminded" ] && grep -qsF REPLACE_WITH_YOUR_GATEWAY_KEY \
      "$HOME/.codex/auth.json" "$HOME/.claude/settings.json" \
      "$HOME/.local/share/code-server/User/chatLanguageModels.json"; then
      echo "Claude Code, Codex, and VS Code chat need your AI gateway key. Run scout-set-key to add it." >&2
      _scout_key_reminded=1
    fi
    ;;
esac
