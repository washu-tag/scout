import { Page } from '@playwright/test';

export interface TestUser {
  username: string;
  password: string;
}

/**
 * Perform the full Scout sign-in flow on the given page.
 *
 * Navigates to `targetUrl`, which triggers the OAuth2 Proxy → Keycloak
 * redirect chain. Uses CDP Fetch to inject `kc_idp_hint=` so Keycloak
 * shows the username/password form instead of auto-redirecting to GitHub.
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

  // Verify we're on the OAuth2 Proxy sign-in page
  await page.waitForSelector('h2');
  const heading = await page.locator('h2').textContent();
  if (heading?.trim() !== 'Welcome to Scout') {
    throw new Error(`Expected sign-in page with "Welcome to Scout", got "${heading}"`);
  }

  // Use CDP Fetch to intercept the Keycloak authorize URL in the redirect chain.
  // page.route() only intercepts the initial request, not server-side 302 redirects.
  // CDP Fetch intercepts ALL requests including redirect hops, so we can inject
  // kc_idp_hint="" to bypass the identity provider auto-redirect and use the
  //  Keycloak login form.
  const cdp = await page.context().newCDPSession(page);
  await cdp.send('Fetch.enable', {
    patterns: [
      {
        urlPattern: '*realms/scout/protocol/openid-connect/auth*',
        requestStage: 'Request',
      },
    ],
  });
  cdp.on('Fetch.requestPaused', async (event) => {
    const url = new URL(event.request.url);
    if (!url.searchParams.has('kc_idp_hint')) {
      url.searchParams.set('kc_idp_hint', '');
      await cdp.send('Fetch.continueRequest', {
        requestId: event.requestId,
        url: url.toString(),
      });
    } else {
      await cdp.send('Fetch.continueRequest', {
        requestId: event.requestId,
      });
    }
  });

  // Click "Sign In" — submits form to OAuth2 Proxy, which redirects to Keycloak
  await page.locator('button.btn').click();

  // Wait for Keycloak login form
  await page.waitForSelector('#username', { timeout: 30000 });

  // Disable CDP intercept before submitting credentials so it doesn't interfere
  // with the post-login redirect chain (callback → original URL)
  await cdp.send('Fetch.disable');
  await cdp.detach();

  // Fill credentials and submit
  await page.fill('#username', username);
  await page.fill('#password', password);
  await page.click('#kc-login');

  // Wait for navigation to settle after the redirect chain completes
  await page.waitForLoadState('domcontentloaded', { timeout: 30000 });
}
