"""
ZAP authentication hook for Scout.

Injects an OAuth2 Proxy session cookie into all requests, enabling
authenticated scanning without ZAP needing to handle the multi-domain
OAuth2/Keycloak login flow.

Usage:
    1. Log into Scout via a browser
    2. Copy the _oauth2_proxy cookie value from browser dev tools
    3. Set the SCOUT_SESSION_COOKIE environment variable
    4. Run ZAP with this hook:

    export SCOUT_SESSION_COOKIE="<paste cookie value here>"

    docker run -v $(pwd)/zap:/zap/wrk/:rw \
      -e SCOUT_SESSION_COOKIE \
      -t ghcr.io/zaproxy/zaproxy:stable \
      zap-full-scan.py \
        -t https://scout.tag.rcif.io/ \
        -J report.json \
        --hook /zap/wrk/auth-hook.py

The cookie is valid for 8 hours (default oauth2_proxy_cookie_expire).
"""

import os
import sys


def zap_started(zap, target):
    """Called after ZAP has started and the target is set up."""
    cookie = os.environ.get("SCOUT_SESSION_COOKIE", "")
    if not cookie:
        print(
            "WARNING: SCOUT_SESSION_COOKIE not set. " "Scan will run unauthenticated.",
            file=sys.stderr,
        )
        return

    # Use ZAP's replacer addon to inject the session cookie into all requests.
    # This adds/replaces the Cookie header on every outgoing request.
    zap.replacer.add_rule(
        description="Scout OAuth2 Proxy Session",
        enabled=True,
        matchtype="REQ_HEADER",
        matchregex=False,
        matchstring="Cookie",
        replacement="_oauth2_proxy=" + cookie,
        initiators="",
    )
    print("Authenticated session cookie configured for scanning.")
