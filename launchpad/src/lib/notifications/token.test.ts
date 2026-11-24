import assert from 'node:assert/strict';
import { before, describe, it } from 'node:test';
import { ISSUER, LAUNCHPAD, testRealm, type Realm } from './testing';
import { SEND_ROLE, verifySenderToken } from './token';

describe('verifySenderToken', () => {
  let realm: Realm;
  const verify = (authorization: string | null) =>
    verifySenderToken(authorization, { issuer: ISSUER, clientId: LAUNCHPAD, keys: realm.keys });

  before(async () => {
    realm = await testRealm();
  });

  it('names the client the token was issued to', async () => {
    assert.deepEqual(await verify(`Bearer ${await realm.token()}`), {
      ok: true,
      clientId: 'request-tracker',
    });
  });

  it('needs a Bearer token', async () => {
    for (const header of [null, '', 'Basic dXNlcjpwYXNz', 'Bearer']) {
      assert.deepEqual(await verify(header), { ok: false, status: 401, error: 'missing_token' });
    }
  });

  it('refuses a token signed by another key, from another issuer, expired, or for another audience', async () => {
    const tokens = [
      await realm.token({}, { key: realm.otherKey }),
      await realm.token({ iss: 'https://keycloak.example.org/realms/scout' }),
      await realm.token({}, { expiresIn: '-2m' }),
      await realm.token({ aud: 'trino' }),
      'not.a.token',
    ];
    for (const token of tokens) {
      assert.deepEqual(await verify(`Bearer ${token}`), {
        ok: false,
        status: 401,
        error: 'invalid_token',
      });
    }
  });

  it('refuses an ID or refresh token, and a token naming no client', async () => {
    for (const claims of [{ typ: 'ID' }, { typ: 'Refresh' }, { azp: undefined }, { azp: '' }]) {
      const result = await verify(`Bearer ${await realm.token(claims)}`);
      assert.deepEqual(result, { ok: false, status: 401, error: 'invalid_token' });
    }
  });

  it("refuses a token issued to one of the client's users", async () => {
    for (const claims of [
      { client_id: undefined, preferred_username: 'alice' },
      { client_id: 'other-app' },
    ]) {
      const result = await verify(`Bearer ${await realm.token(claims)}`);
      assert.deepEqual(result, { ok: false, status: 403, error: 'service_account_required' });
    }
  });

  it('needs the send role on the launchpad client, not on any other', async () => {
    const tokens = [
      await realm.token({ resource_access: {} }),
      await realm.token({ resource_access: { [LAUNCHPAD]: { roles: ['launchpad-user'] } } }),
      await realm.token({ resource_access: { 'request-tracker': { roles: [SEND_ROLE] } } }),
      await realm.token({ resource_access: undefined, realm_access: { roles: [SEND_ROLE] } }),
      // What Keycloak issues a client without any launchpad role: not addressed to it either.
      await realm.token({
        aud: 'account',
        resource_access: { account: { roles: ['view-profile'] } },
      }),
    ];
    for (const token of tokens) {
      assert.deepEqual(await verify(`Bearer ${token}`), {
        ok: false,
        status: 403,
        error: 'missing_role',
      });
    }
  });

  it("answers 503 when the realm's keys can't be fetched", async () => {
    const result = await verifySenderToken(`Bearer ${await realm.token()}`, {
      issuer: ISSUER,
      clientId: LAUNCHPAD,
      keys: async () => {
        throw new TypeError('fetch failed');
      },
    });
    assert.deepEqual(result, { ok: false, status: 503, error: 'keys_unavailable' });
  });
});
