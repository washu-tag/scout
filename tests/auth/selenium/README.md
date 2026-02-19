# Scout Auth QA — Selenium (Java)

Selenium-based authorization tests for Scout's OAuth2 Proxy + Keycloak auth layer.

These tests verify that unauthorized users (not in the `scout-user` Keycloak group) receive a **403 Access Pending** page when accessing any Scout service.

## Prerequisites

- **Java 21+** (JDK)
- **Chrome** browser installed
- **ChromeDriver** matching your Chrome version (or use [Selenium Manager](https://www.selenium.dev/documentation/selenium_manager/) which auto-downloads it)
- Network access to the Scout cluster

## Setup

```bash
cd tests/auth/selenium
cp .env.example .env
# Edit .env with your Scout hostname and Keycloak admin password
```

## Run Tests

```bash
./gradlew test
```

## Project Structure

```
src/test/java/scout/auth/
├── config/
│   └── TestConfig.java        # .env loading + env var access
├── helpers/
│   ├── KeycloakAdmin.java     # Keycloak Admin REST API client
│   └── ScoutAuth.java         # Scout sign-in flow (OAuth2 Proxy → Keycloak)
├── AuthorizationTest.java     # Authorization test cases
└── TestLifecycle.java         # JUnit extension: user setup/teardown, WebDriver lifecycle
```

## How It Works

1. **Setup**: Creates ephemeral test users in Keycloak (unauthorized + authorized)
2. **Sign-in**: Navigates through OAuth2 Proxy → Keycloak login, using `java.net.http.HttpClient` to capture the Keycloak redirect URL and inject `kc_idp_hint=""` (bypasses GitHub IDP auto-redirect)
3. **Assert**: Verifies the error page content (`<h1>Access Pending</h1>`, title contains `403`, `#return-to-scout` link visible)
4. **Teardown**: Deletes test users from Keycloak

## Phase 1 Tests

- **Launchpad** (`https://<hostname>/`) → 403 Access Pending
- **Nonexistent Service** (`https://nonexistent.<hostname>/`) → 403 Access Pending
