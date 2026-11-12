import { randomUUID } from 'crypto';
import { test, expect } from '@playwright/test';
import { KeycloakAdmin } from '../helpers/keycloak-admin';
import { signInToScout } from '../helpers/scout-auth';

const hostname = process.env.SCOUT_HOSTNAME!;
const password = process.env.TEST_USER_PASSWORD!;

// UUID per run: the iframe-defaults Event function fires on user.created (first
// OAuth login for a given identity), so a static user would silently no-op.
test('new users get iframeSandbox settings enabled', async ({ page }) => {
  const username = `iframe-seed-${randomUUID()}`;

  const keycloak = new KeycloakAdmin();
  await keycloak.createUser({
    username,
    password,
    email: `${username}@scout.test`,
    firstName: 'IframeSeed',
    lastName: 'Test',
    groups: ['scout-user'],
  });

  await signInToScout(page, `https://chat.${hostname}/`, { username, password });

  // Wait for the JWT the OAuth callback stores (0.13's socket.io never lets networkidle settle).
  // waitForFunction retries an evaluation the sign-in redirect cut off and throws on real errors.
  const token = await (
    await page.waitForFunction(() => localStorage.getItem('token'), undefined, { timeout: 30000 })
  ).jsonValue();

  await expect
    .poll(
      async () => {
        const resp = await page.request.get(`https://chat.${hostname}/api/v1/users/user/settings`, {
          headers: { Authorization: `Bearer ${token}` },
        });
        if (!resp.ok()) return null;
        const body = (await resp.json()) as {
          ui?: { iframeSandboxAllowSameOrigin?: boolean; iframeSandboxAllowForms?: boolean };
        };
        return {
          sameOrigin: body?.ui?.iframeSandboxAllowSameOrigin ?? null,
          forms: body?.ui?.iframeSandboxAllowForms ?? null,
        };
      },
      { timeout: 15000, intervals: [500, 1000, 2000, 3000] },
    )
    .toEqual({ sameOrigin: true, forms: true });
});
