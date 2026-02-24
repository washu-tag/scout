import { Page } from '@playwright/test';

export interface TestUser {
  username: string;
  password: string;
}

/**
 * Perform the full Scout sign-in flow on the given page.
 *
 * Navigates to `targetUrl`, which triggers the OAuth2 Proxy → Keycloak
 * redirect chain. The identity-provider-redirector is disabled in global
 * setup so Keycloak shows the username/password form directly.
 *
 * After this function returns, the page is on the final post-login page
 * (either "Access Pending" 403 or the actual service page) and the
 * `_oauth2_proxy` cookie is set in the page's browser context.
 */
export async function signInToScout(page: Page, targetUrl: string, user: TestUser): Promise<void> {
  const { username, password } = user;

  // Navigate to target — OAuth2 Proxy serves the sign-in page as 401
  const signInResponse = await page.goto(targetUrl, { waitUntil: 'domcontentloaded' });
  if (signInResponse?.status() !== 401) {
    throw new Error(`Expected 401 sign-in page at ${targetUrl}, got ${signInResponse?.status()}`);
  }

  // Click "Sign In" — submits form to OAuth2 Proxy, which redirects to Keycloak
  await page.locator('button.btn').click();

  // Wait for Keycloak login form
  await page.waitForSelector('#username', { timeout: 30000 });

  // Fill credentials and submit
  await page.fill('#username', username);
  await page.fill('#password', password);
  await page.click('#kc-login');

  // Wait for navigation to settle after the redirect chain completes
  await page.waitForLoadState('domcontentloaded', { timeout: 30000 });
}
