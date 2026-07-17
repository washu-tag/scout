# Auth Tests

End-to-end tests against a running Scout requiring auth. Curl-based tests probe the ingress unauthenticated. Playwright tests drive a real Keycloak-authenticated user session through the browser. Home for tests that needs an authenticated Scout session.

For comprehensive documentation, see the [Testing](../../docs/internal/authentication.md#testing) section of the authentication documentation.

## Quick Start

**Curl tests** (unauthenticated access):

```bash
./auth-curl-tests.sh scout.example.com
```

**Playwright tests** (browser-based tests):

```bash
cp .env.example .env
# Edit .env
npm install
npm test
```
