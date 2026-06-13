import { test, expect } from '@playwright/test';
import { signInToScout, TestUser } from '../helpers/scout-auth';

const hostname = process.env.SCOUT_HOSTNAME!;

const authorizedUser: TestUser = {
  username: process.env.AUTHORIZED_USER_USERNAME!,
  password: process.env.TEST_USER_PASSWORD!,
};

const unauthorizedUser: TestUser = {
  username: process.env.UNAUTHORIZED_USER_USERNAME!,
  password: process.env.TEST_USER_PASSWORD!,
};

const managerUser: TestUser = {
  username: process.env.MANAGER_USER_USERNAME!,
  password: process.env.TEST_USER_PASSWORD!,
};

// Unauthorized user, no scout-user group membership, 403 Access Pending everywhere

const protectedServices = [
  // Root URLs for all Scout services
  { name: 'Launchpad', url: `https://${hostname}/` },
  { name: 'Superset', url: `https://superset.${hostname}/` },
  { name: 'JupyterHub', url: `https://jupyter.${hostname}/` },
  { name: 'Grafana', url: `https://grafana.${hostname}/` },
  { name: 'Temporal', url: `https://temporal.${hostname}/` },
  { name: 'MinIO', url: `https://minio.${hostname}/` },
  { name: 'Open WebUI', url: `https://chat.${hostname}/` },
  { name: 'Playbooks', url: `https://playbooks.${hostname}/` },
  { name: 'Nonexistent Service', url: `https://nonexistent.${hostname}/` },
  // Deep service paths to verify inner pages are also blocked, not just root URLs
  { name: 'Superset SQL Lab', url: `https://superset.${hostname}/sqllab/` },
  { name: 'JupyterHub Spawn', url: `https://jupyter.${hostname}/hub/spawn` },
  { name: 'Grafana Dashboards', url: `https://grafana.${hostname}/dashboards` },
  { name: 'Temporal Workflows', url: `https://temporal.${hostname}/namespaces/default/workflows` },
  { name: 'MinIO Browser', url: `https://minio.${hostname}/browser/lake/hl7` },
  {
    name: 'Playbooks Notebook',
    url: `https://playbooks.${hostname}/voila/render/cohort/Cohort.ipynb`,
  },
];

test.describe('Unauthorized User', () => {
  for (const { name, url } of protectedServices) {
    test(`${name} returns 403 Access Pending`, async ({ page }) => {
      await signInToScout(page, url, unauthorizedUser);
      const response = await page.reload({ waitUntil: 'domcontentloaded' });
      expect(response?.status()).toBe(403);
    });
  }
});

// Authorized user (scout-user, non-admin): denied access to admin services

test.describe('Authorized Non-Admin User', () => {
  test('Launchpad hides admin links', async ({ page }) => {
    const url = `https://${hostname}/`;
    await signInToScout(page, url, authorizedUser);

    // Wait for React app to finish loading and rendering
    await page.waitForLoadState('networkidle', { timeout: 15000 });

    // Admin Tools section should NOT be visible for non-admin users
    await expect(page.locator('text=Admin Tools')).toBeHidden();
  });

  test('Temporal denies access', async ({ page }) => {
    const url = `https://temporal.${hostname}/`;
    await signInToScout(page, url, authorizedUser);

    // Set up response listeners before navigating so we capture the 403 authorization
    // denials during the SSO callback instead of checking page content
    const namespaceDenied = page.waitForResponse(
      (resp) =>
        resp.url().includes(`temporal.${hostname}/api/v1/namespaces`) && resp.status() === 403,
      { timeout: 30000 },
    );
    const clusterInfoDenied = page.waitForResponse(
      (resp) =>
        resp.url().includes(`temporal.${hostname}/api/v1/cluster-info`) && resp.status() === 403,
      { timeout: 30000 },
    );
    await page.goto(`https://temporal.${hostname}/auth/sso`);
    const [namespaceResp, clusterInfoResp] = await Promise.all([
      namespaceDenied,
      clusterInfoDenied,
    ]);
    expect(namespaceResp.status()).toBe(403);
    expect(clusterInfoResp.status()).toBe(403);
  });

  test('MinIO denies access', async ({ page }) => {
    const url = `https://minio.${hostname}/`;
    await signInToScout(page, url, authorizedUser);

    // Trigger MinIO SSO via the login page's SSO button (use loose selector, not exact text)
    await page.click('button:has-text("Login")', { timeout: 15000 });
    await page.waitForLoadState('networkidle', { timeout: 60000 });

    // After SSO, MinIO API should reject users without policy claim
    const response = await page.request.get(`https://minio.${hostname}/api/v1/buckets`);
    expect(response.status()).toBe(403);
  });

  test('Grafana datasources API returns 403', async ({ page }) => {
    await signInToScout(page, `https://grafana.${hostname}/`, authorizedUser);

    // Grafana allows entry to the UI but gives the user no permissions, so we hit the
    // datasources API directly and assert the 403 status code instead of loading the UI.
    const response = await page.request.get(`https://grafana.${hostname}/api/datasources/`);
    expect(response.status()).toBe(403);
  });

  test('Keycloak Admin API returns 403', async ({ page }) => {
    await signInToScout(page, `https://${hostname}/`, authorizedUser);

    // Navigate to the admin console so the Keycloak session is established,
    // then assert on the /admin/serverinfo call which returns 403
    const denied = page.waitForResponse(
      (resp) =>
        resp.url().includes(`keycloak.${hostname}/admin/serverinfo`) && resp.status() === 403,
      { timeout: 60000 },
    );
    await page.goto(`https://keycloak.${hostname}/admin/scout/console/`);
    const response = await denied;
    expect(response.status()).toBe(403);
  });

  test('Nonexistent subdomain redirects to Launchpad', async ({ page }) => {
    const url = `https://nonexistent.${hostname}/`;
    await signInToScout(page, url, authorizedUser);

    // The catch-all ingress redirect-to-launchpad middleware rewrites
    // unknown subdomains to the root hostname, so the user should land
    // on Launchpad after the redirect chain settles.
    await page.waitForURL(`https://${hostname}/`, { timeout: 30000 });
    const response = await page.reload({ waitUntil: 'domcontentloaded' });
    expect(response?.status()).toBe(200);
  });
});

// User manager (scout-user + scout-user-manager): runs the user console via the
// manage-users capability, but is not a realm admin and not an admin of any
// other Scout service (ADR 0026).

test.describe('User Manager', () => {
  test('Launchpad shows user management without infra tiles', async ({ page }) => {
    await signInToScout(page, `https://${hostname}/`, managerUser);
    await page.waitForLoadState('networkidle', { timeout: 15000 });

    // Manager sees the management card with the Users tile only — no admin
    // heading and none of the admin-only infrastructure tiles.
    await expect(page.locator('text=User Management')).toBeVisible();
    await expect(page.locator('text=Admin Tools')).toBeHidden();
    // Exact match: "Monitor" would otherwise substring-match the
    // "Clinical Follow-up Monitoring" playbook tile.
    await expect(page.getByText('Orchestrator', { exact: true })).toBeHidden();
    await expect(page.getByText('Monitor', { exact: true })).toBeHidden();
  });

  test('User console is accessible', async ({ page }) => {
    await signInToScout(page, `https://${hostname}/admin/users`, managerUser);
    await page.waitForLoadState('networkidle', { timeout: 15000 });

    // The capability gate passed: the console renders its tabs rather than
    // the not-authorized notice, and the schema/users fetches succeeded.
    await expect(page.locator('text=You need to be a Scout admin or user manager')).toBeHidden();
    await expect(page.getByRole('button', { name: 'Pending' })).toBeVisible();
  });

  test('Keycloak Admin API returns 403 (manager is not realm-admin)', async ({ page }) => {
    await signInToScout(page, `https://${hostname}/`, managerUser);

    const denied = page.waitForResponse(
      (resp) =>
        resp.url().includes(`keycloak.${hostname}/admin/serverinfo`) && resp.status() === 403,
      { timeout: 60000 },
    );
    await page.goto(`https://keycloak.${hostname}/admin/scout/console/`);
    const response = await denied;
    expect(response.status()).toBe(403);
  });

  test('Grafana datasources API returns 403', async ({ page }) => {
    await signInToScout(page, `https://grafana.${hostname}/`, managerUser);

    const response = await page.request.get(`https://grafana.${hostname}/api/datasources/`);
    expect(response.status()).toBe(403);
  });

  test('Temporal denies access', async ({ page }) => {
    const url = `https://temporal.${hostname}/`;
    await signInToScout(page, url, managerUser);

    const namespaceDenied = page.waitForResponse(
      (resp) =>
        resp.url().includes(`temporal.${hostname}/api/v1/namespaces`) && resp.status() === 403,
      { timeout: 30000 },
    );
    await page.goto(`https://temporal.${hostname}/auth/sso`);
    const namespaceResp = await namespaceDenied;
    expect(namespaceResp.status()).toBe(403);
  });

  test('MinIO denies access', async ({ page }) => {
    const url = `https://minio.${hostname}/`;
    await signInToScout(page, url, managerUser);

    await page.click('button:has-text("Login")', { timeout: 15000 });
    await page.waitForLoadState('networkidle', { timeout: 60000 });

    const response = await page.request.get(`https://minio.${hostname}/api/v1/buckets`);
    expect(response.status()).toBe(403);
  });
});
