# Auth QA Tests

Authorization QA tests for Scout services.

## Unauthenticated Access Tests

Curl-based tests that verify all protected Scout endpoints reject unauthenticated requests (no cookies, no tokens) and that unprotected endpoints (Keycloak, OAuth2 Proxy sign-in) remain accessible. Runs the full test matrix over both HTTPS and HTTP.

### Prerequisites

- `curl` installed
- Network access to the target Scout deployment

### Usage

```bash
# Basic usage
./authz-curl-test.sh scout.example.com

# With custom timeout
./authz-curl-test.sh scout.example.com --timeout 15

```

### Options

| Argument/Flag | Required | Default | Description |
|---------------|----------|---------|-------------|
| `<hostname>` | Yes | — | Scout base hostname (first argument) |
| `--timeout` | No | 10 | curl timeout in seconds |
| `--help` | No | — | Show usage |

### Adding Tests

Edit the `TESTS` array in `authz-curl-test.sh` and add a line with 5 space-separated fields:

```bash
subdomain /path METHOD expected_status "Description"
```

For example:

```bash
superset POST /api/v1/query/ 401 "Superset query API"
```

- Use empty string `""` for the root hostname (Launchpad)
- `401` — protected endpoint, must reject with exact status code (no redirects, no content)
- `200` — unprotected endpoint, must be directly accessible

### Exit Codes

- `0` — All tests passed
- `1` — One or more tests failed
- `2` — Configuration error (e.g., malformed test definitions)
