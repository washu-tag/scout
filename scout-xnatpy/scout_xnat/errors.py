class ScoutXnatAuthError(RuntimeError):
    """Raised when the bearer-token bootstrap fails (env vars missing,
    Hub API rejected, XNAT rejected the token, etc.)."""


class XnatBearerRejectedError(ScoutXnatAuthError):
    """XNAT replied 401/403 to the JSESSIONID-mint request — i.e. the
    bearer token was syntactically valid but XNAT didn't accept it.
    Callers should treat this as an auth failure (401)."""


class XnatUnreachableError(ScoutXnatAuthError):
    """The JSESSIONID-mint TCP/HTTP request failed to reach XNAT (DNS,
    connection refused, timeout, etc.) — distinct from XNAT actively
    rejecting the bearer. Callers should treat this as an upstream
    outage (502/503)."""
