# Auth Tests

Authorization tests for Scout with curl-based unauthenticated access tests and Playwright browser-based authorization tests that verify OAuth2 Proxy + Keycloak across all Scout services.

For comprehensive documentation, see the [Testing](../../docs/internal/authentication.md#testing) section of the authentication documentation.

## Quick Start

**Curl tests** (unauthenticated access):

```bash
./auth-curl-tests.sh scout.example.com
```

**Playwright tests** (browser-based authorization):

```bash
cp .env.example .env
# Edit .env
npm install
npm test
```
