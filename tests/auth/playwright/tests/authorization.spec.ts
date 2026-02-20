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
  // Deep service paths — verify inner pages are also blocked, not just root URLs
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
      await expect(page.locator('h1')).toHaveText('Access Pending');
      await expect(page.locator('#return-to-scout')).toBeVisible();
    });
  }
});

// Authorized user (scout-user, non-admin): denied access to admin services

test.describe('Authorized Non-Admin User', () => {
  test('Launchpad hides admin links', async ({ page }) => {
    const url = `https://${hostname}/`;
    await signInToScout(page, url, authorizedUser);

    // Wait for React to load, "Core Services" confirms the session loaded and page rendered
    await page.waitForSelector('text=Core Services', { timeout: 15000 });

    // Admin Tools section should NOT be visible for non-admin users
    await expect(page.locator('text=Admin Tools')).toBeHidden();
  });

  test('Grafana denies access', async ({ page }) => {
    const url = `https://grafana.${hostname}/`;
    await signInToScout(page, url, authorizedUser);

    await expect(page.locator('text=Unauthorized')).toBeVisible({ timeout: 60000 });
  });

  test('Temporal denies access', async ({ page }) => {
    const url = `https://temporal.${hostname}/`;
    await signInToScout(page, url, authorizedUser);

    // Click "Continue to SSO" to complete Temporal's own auth flow
    await page.click('text=Continue to SSO', { timeout: 30000 });

    // Temporal should show unauthorized for non-admin users
    await expect(page.locator('text=Request unauthorized')).toBeVisible({ timeout: 30000 });
  });

  test('MinIO denies access', async ({ page }) => {
    const url = `https://minio.${hostname}/`;
    await signInToScout(page, url, authorizedUser);

    // Click "Login with SSO (PRIMARY_IAM)" to complete MinIO's own auth flow
    await page.click('text=Login with SSO (PRIMARY_IAM)', { timeout: 15000 });

    // Expect policy claim error for non-admin users
    await expect(
      page.locator(
        'text=Policy claim missing from the JWT token, credentials will not be generated',
      ),
    ).toBeVisible({ timeout: 60000 });
  });

  test('Grafana Authentication dashboard returns 403', async ({ page }) => {
    const url = `https://grafana.${hostname}/d/auth_dashboard_01`;
    await signInToScout(page, url, authorizedUser);

    await expect(page.locator('text=Failed to load dashboard')).toBeVisible({ timeout: 60000 });
    await expect(page.getByText('"status":403')).toBeVisible({ timeout: 60000 });
  });

  test('Grafana Kubernetes dashboard returns 403', async ({ page }) => {
    const url = `https://grafana.${hostname}/d/scout_kubernetes_dashboard_01/`;
    await signInToScout(page, url, authorizedUser);

    await expect(page.locator('text=Failed to load dashboard')).toBeVisible({ timeout: 60000 });
    await expect(page.getByText('"status":403')).toBeVisible({ timeout: 60000 });
  });

  // Note: The admin console SPA itself loads with 200; the 403 is on the
  // /admin/serverinfo XHR, so we only assert the visible error message here.
  test('Keycloak Admin Console denies access', async ({ page }) => {
    await signInToScout(page, `https://${hostname}/`, authorizedUser);

    await page.goto(`https://keycloak.${hostname}/admin/scout/console/`);

    await expect(
      page.locator('text=You do not have permission to access this resource'),
    ).toBeVisible({ timeout: 60000 });
  });
});
