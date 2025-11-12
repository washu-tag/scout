#!/usr/bin/env python3
"""
OIDC Backchannel Logout Service for JupyterHub

Receives backchannel logout notifications from Keycloak and terminates
user JupyterHub sessions via the REST API.

This service runs as a sidecar container alongside the JupyterHub hub.
"""

from flask import Flask, request
from jose import jwt, JWTError
import requests
import os
import logging

app = Flask(__name__)

# Configure logging
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()
logging.basicConfig(
    level=getattr(logging, LOG_LEVEL, logging.INFO),
    format="[%(asctime)s] %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)

# Configuration from environment variables
KEYCLOAK_JWKS_URL = os.getenv("KEYCLOAK_JWKS_URL")
KEYCLOAK_ISSUER = os.getenv("KEYCLOAK_ISSUER")
CLIENT_ID = os.getenv("CLIENT_ID")
JUPYTERHUB_API_URL = os.getenv("JUPYTERHUB_API_URL", "http://localhost:8081/hub/api")
JUPYTERHUB_API_TOKEN = os.getenv("JUPYTERHUB_API_TOKEN")

# Validate required configuration
required_vars = {
    "KEYCLOAK_JWKS_URL": KEYCLOAK_JWKS_URL,
    "KEYCLOAK_ISSUER": KEYCLOAK_ISSUER,
    "CLIENT_ID": CLIENT_ID,
    "JUPYTERHUB_API_TOKEN": JUPYTERHUB_API_TOKEN,
}

for var_name, var_value in required_vars.items():
    if not var_value:
        logger.error(f"Missing required environment variable: {var_name}")
        raise ValueError(f"Missing required environment variable: {var_name}")

logger.info("Backchannel logout service starting...")
logger.info(f"LOG_LEVEL: {LOG_LEVEL}")
logger.info(f"KEYCLOAK_ISSUER: {KEYCLOAK_ISSUER}")
logger.info(f"CLIENT_ID: {CLIENT_ID}")
logger.info(f"JUPYTERHUB_API_URL: {JUPYTERHUB_API_URL}")
logger.debug(f"KEYCLOAK_JWKS_URL: {KEYCLOAK_JWKS_URL}")


def fetch_jwks():
    """Fetch JWKS from Keycloak for JWT validation"""
    try:
        logger.debug(f"Fetching JWKS from: {KEYCLOAK_JWKS_URL}")
        response = requests.get(KEYCLOAK_JWKS_URL, timeout=10)
        response.raise_for_status()
        jwks = response.json()
        logger.debug(f"Successfully fetched JWKS with {len(jwks.get('keys', []))} keys")
        return jwks
    except Exception as e:
        logger.error(f"Failed to fetch JWKS from {KEYCLOAK_JWKS_URL}: {e}")
        raise


def validate_logout_token(token_string):
    """
    Validate OIDC backchannel logout token according to spec:
    https://openid.net/specs/openid-connect-backchannel-1_0.html

    The logout token MUST:
    - Be a valid JWT signed by the OP
    - Contain iss (issuer) and aud (audience) claims
    - Contain an events claim with backchannel-logout event
    - Contain either sid (session ID) or sub (subject) claim
    - NOT contain a nonce claim
    """
    try:
        logger.debug("Starting logout token validation")
        logger.debug(f"Token length: {len(token_string)} characters")

        # Fetch JWKS for signature validation
        jwks = fetch_jwks()

        logger.debug(
            f"Decoding JWT with audience={CLIENT_ID}, issuer={KEYCLOAK_ISSUER}"
        )

        # Decode and validate JWT
        claims = jwt.decode(
            token_string,
            jwks,
            algorithms=["RS256"],
            audience=CLIENT_ID,
            issuer=KEYCLOAK_ISSUER,
            options={
                "verify_signature": True,
                "verify_aud": True,
                "verify_iss": True,
                "verify_exp": True,
            },
        )

        logger.debug(f"JWT decoded successfully. Claims keys: {list(claims.keys())}")
        logger.debug(f"Full token claims: {claims}")
        logger.debug(f"Token issuer: {claims.get('iss')}")
        logger.debug(f"Token audience: {claims.get('aud')}")
        logger.debug(f"Token subject: {claims.get('sub')}")
        logger.debug(f"Token session ID: {claims.get('sid')}")

        # Verify backchannel logout event claim
        events = claims.get("events", {})
        logger.debug(f"Events claim: {list(events.keys())}")
        if "http://schemas.openid.net/event/backchannel-logout" not in events:
            logger.error(
                f"Missing backchannel-logout event. Found events: {list(events.keys())}"
            )
            raise ValueError("Missing backchannel-logout event in claims")

        # Verify no nonce (per spec - section 2.6)
        if "nonce" in claims:
            logger.error(
                "Logout token contains nonce claim, which is forbidden by spec"
            )
            raise ValueError("Logout token MUST NOT contain nonce claim")

        # Must have either sid or sub
        if not claims.get("sid") and not claims.get("sub"):
            logger.error("Logout token missing both sid and sub claims")
            raise ValueError("Logout token must contain either sid or sub claim")

        username = claims.get("preferred_username", claims.get("sub"))
        logger.info(f"Valid logout token for user: {username}")
        logger.debug(f"All token claims validated successfully")
        return claims

    except JWTError as e:
        logger.error(f"JWT validation error: {e}")
        logger.debug(f"JWT error details: {type(e).__name__}", exc_info=True)
        raise ValueError(f"Invalid JWT token: {e}")
    except Exception as e:
        logger.error(f"Token validation error: {e}")
        logger.debug(f"Validation error details", exc_info=True)
        raise ValueError(f"Token validation failed: {e}")


def stop_jupyterhub_server(username):
    """
    Stop a user's JupyterHub server via REST API

    API Docs: https://jupyterhub.readthedocs.io/en/stable/reference/rest-api.html
    DELETE /hub/api/users/:name/server
    """
    try:
        url = f"{JUPYTERHUB_API_URL}/users/{username}/server"
        headers = {
            "Authorization": f"Bearer {JUPYTERHUB_API_TOKEN}",
            "Content-Type": "application/json",
        }

        logger.info(f"Stopping server for user: {username}")
        logger.debug(f"JupyterHub API request URL: {url}")
        logger.debug(
            f"Request headers (token redacted): Authorization: Bearer *****, Content-Type: {headers['Content-Type']}"
        )

        response = requests.delete(url, headers=headers, timeout=10)

        logger.debug(f"JupyterHub API response status: {response.status_code}")
        logger.debug(f"JupyterHub API response headers: {dict(response.headers)}")
        if response.text:
            logger.debug(f"JupyterHub API response body: {response.text}")

        if response.status_code == 204:
            logger.info(f"Successfully stopped server for user: {username}")
            return True
        elif response.status_code == 202:
            logger.info(f"Server stop pending for user: {username}")
            return True
        elif response.status_code == 400:
            logger.warning(
                f"Server not running or already stopping for user: {username}"
            )
            logger.debug(f"400 response details: {response.text}")
            return True
        elif response.status_code == 404:
            logger.warning(f"User not found: {username}")
            logger.debug(f"404 response details: {response.text}")
            return True
        else:
            logger.error(
                f"Unexpected status {response.status_code} when stopping server for {username}: {response.text}"
            )
            return False

    except requests.exceptions.Timeout as e:
        logger.error(f"Timeout calling JupyterHub API for user {username}: {e}")
        logger.debug(f"Timeout details", exc_info=True)
        return False
    except requests.exceptions.ConnectionError as e:
        logger.error(
            f"Connection error calling JupyterHub API for user {username}: {e}"
        )
        logger.debug(f"Connection error details", exc_info=True)
        return False
    except requests.exceptions.RequestException as e:
        logger.error(f"Failed to call JupyterHub API for user {username}: {e}")
        logger.debug(f"Request error details", exc_info=True)
        return False


@app.route("/backchannel-logout", methods=["POST"])
def backchannel_logout():
    """
    OIDC Backchannel Logout Endpoint

    Receives logout_token from Keycloak and terminates the user's JupyterHub session.

    Per spec (https://openid.net/specs/openid-connect-backchannel-1_0.html):
    - Must accept application/x-www-form-urlencoded
    - Must validate the logout_token
    - Must respond with 200 OK or 501 Not Implemented
    - Should respond quickly (async processing recommended)
    """
    try:
        logger.info("Received backchannel logout request")
        logger.debug(f"Request method: {request.method}")
        logger.debug(f"Request content type: {request.content_type}")
        logger.debug(f"Request headers: {dict(request.headers)}")
        logger.debug(f"Request form keys: {list(request.form.keys())}")

        # Get logout token from form data
        logout_token = request.form.get("logout_token")
        if not logout_token:
            logger.error("Missing logout_token in request")
            logger.debug(f"Request form data: {request.form}")
            return "Bad Request: Missing logout_token", 400

        logger.debug(f"Received logout_token (length: {len(logout_token)})")

        # Validate the logout token
        try:
            claims = validate_logout_token(logout_token)
        except ValueError as e:
            logger.error(f"Invalid logout token: {e}")
            # Per spec: Return 400 for invalid tokens
            return f"Bad Request: {e}", 400

        # Extract username from claims
        # Prefer preferred_username, fallback to sub
        username = claims.get("preferred_username") or claims.get("sub")

        if not username:
            logger.error("Could not extract username from logout token")
            logger.debug(f"Claims available: {list(claims.keys())}")
            return "Bad Request: No username in token", 400

        logger.info(f"Processing logout for user: {username}")

        # Stop the user's JupyterHub server
        success = stop_jupyterhub_server(username)

        if success:
            logger.info(f"Logout completed successfully for user: {username}")
            # Always return 200 OK to Keycloak (per spec)
            return "OK", 200
        else:
            logger.error(f"Failed to stop server for user: {username}")
            # Still return 200 to prevent Keycloak retry storm
            # Errors are logged for investigation
            return "OK", 200

    except Exception as e:
        logger.error(f"Unexpected error in backchannel logout: {e}", exc_info=True)
        logger.debug(f"Exception type: {type(e).__name__}")
        # Return 200 even on error to prevent Keycloak retries
        # Log the error for investigation
        return "OK", 200


@app.route("/health", methods=["GET"])
def health():
    """Health check endpoint for Kubernetes liveness/readiness probes"""
    return "OK", 200


@app.route("/", methods=["GET"])
def root():
    """Root endpoint for basic info"""
    return {
        "service": "jupyterhub-backchannel-logout",
        "status": "running",
        "endpoints": {
            "/backchannel-logout": "POST - OIDC backchannel logout",
            "/health": "GET - Health check",
        },
    }, 200


if __name__ == "__main__":
    logger.info("Starting backchannel logout service on port 8080")
    # Run on all interfaces, port 8080
    app.run(host="0.0.0.0", port=8080)
