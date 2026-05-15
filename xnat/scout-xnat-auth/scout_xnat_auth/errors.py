class ScoutXnatAuthError(RuntimeError):
    """Raised when the bearer-token bootstrap fails (env vars missing,
    Hub API rejected, XNAT rejected the token, etc.)."""
