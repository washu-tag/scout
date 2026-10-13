import { test, expect } from '@playwright/test';
import { signInToScout, TestUser } from '../helpers/scout-auth';

// This proof needs only the Flux auth leg: Keycloak, OAuth2 Proxy and Launchpad.
// The existing setup creates the same two users used by the full platform suite.
const launchpad = `https://${process.env.SCOUT_HOSTNAME}/`;
const authorizedUser: TestUser = {
  username: process.env.AUTHORIZED_USER_USERNAME!,
  password: process.env.TEST_USER_PASSWORD!,
};
const unauthorizedUser: TestUser = {
  username: process.env.UNAUTHORIZED_USER_USERNAME!,
  password: process.env.TEST_USER_PASSWORD!,
};

test.describe('Flux platform authentication', () => {
  test('a fresh browser receives the 401 sign-in boundary', async ({ page }) => {
    const response = await page.goto(launchpad, { waitUntil: 'domcontentloaded' });
    expect(response?.status()).toBe(401);
    await expect(page.locator('button.btn')).toBeVisible();
  });

  test('a signed-in user without scout-user approval receives 403', async ({ page }) => {
    // OAuth2 Proxy denies the callback before saving a session or redirecting
    // to Launchpad. Capture that original response: reloading a one-use OAuth
    // callback could instead produce a CSRF/code error and falsely pass.
    const callback = page.waitForResponse((response) => {
      const url = new URL(response.url());
      return (
        response.request().isNavigationRequest() &&
        response.frame() === page.mainFrame() &&
        url.origin === `https://auth.${process.env.SCOUT_HOSTNAME}` &&
        url.pathname === '/oauth2/callback'
      );
    });
    const [, response] = await Promise.all([
      signInToScout(page, launchpad, unauthorizedUser),
      callback,
    ]);
    expect(response.status()).toBe(403);
  });

  test('an approved user reaches Launchpad with an authenticated app session', async ({ page }) => {
    await signInToScout(page, launchpad, authorizedUser);

    // Launchpad performs its own NextAuth SSO after OAuth2 Proxy admits the user.
    // A 200 HTML shell alone would miss a broken client secret or callback flow.
    await expect
      .poll(
        async () => {
          const response = await page.request.get(`${launchpad}api/auth/session`);
          if (response.status() !== 200) return null;
          const session = await response.json();
          return session.user ?? null;
        },
        { timeout: 60000, message: 'Launchpad establishes the approved user session' },
      )
      .toMatchObject({ username: authorizedUser.username, isAdmin: false });

    await page.waitForURL(launchpad);
    const response = await page.reload({ waitUntil: 'domcontentloaded' });
    expect(response?.status()).toBe(200);
    await expect(page.getByText('Admin Tools', { exact: true })).toBeHidden();
  });
});
