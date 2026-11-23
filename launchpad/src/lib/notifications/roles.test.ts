import assert from 'node:assert/strict';
import { afterEach, beforeEach, describe, it, mock } from 'node:test';
import { keycloakRoleDirectory, RoleLookupError, staticRoleDirectory } from './roles';

const ISSUER = 'https://keycloak.scout.example.edu/realms/scout';

function json(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  });
}

describe('keycloakRoleDirectory', () => {
  let calls: { url: string; init?: RequestInit }[];
  let answers: Response[];

  beforeEach(() => {
    process.env.KEYCLOAK_ISSUER = ISSUER;
    calls = [];
    answers = [];
    mock.method(globalThis, 'fetch', async (url: string, init?: RequestInit) => {
      calls.push({ url: String(url), init });
      return answers.shift() ?? json(500, {});
    });
  });

  afterEach(() => {
    mock.restoreAll();
    delete process.env.KEYCLOAK_ISSUER;
  });

  it("asks scout-users for a role's members with the launchpad's own token, reused while valid", async () => {
    answers = [
      json(200, { access_token: 'launchpad-token', expires_in: 300 }),
      json(200, { usernames: ['Alice', 'bob'], truncated: false }),
      json(200, { usernames: [], truncated: false }),
    ];
    const roles = keycloakRoleDirectory(ISSUER);
    assert.deepEqual(await roles.members('request-tracker', 'reviewer', 1000), {
      usernames: ['alice', 'bob'],
      truncated: false,
    });
    await roles.members('request tracker', 'a/b', 5);
    assert.equal(calls[0].url, `${ISSUER}/protocol/openid-connect/token`);
    assert.match(String(calls[0].init?.body), /grant_type=client_credentials/);
    assert.equal(
      calls[1].url,
      `${ISSUER}/scout-users/clients/request-tracker/roles/reviewer/members?max=1000`,
    );
    assert.deepEqual(calls[1].init?.headers, { Authorization: 'Bearer launchpad-token' });
    assert.equal(
      calls[2].url,
      `${ISSUER}/scout-users/clients/request%20tracker/roles/a%2Fb/members?max=5`,
    );
    assert.equal(calls.length, 3);
  });

  it('reports a role the client lacks as null', async () => {
    answers = [
      json(200, { access_token: 't', expires_in: 300 }),
      json(404, { error: 'role_not_found' }),
    ];
    assert.equal(await keycloakRoleDirectory(ISSUER).members('c', 'r', 10), null);
  });

  it('fails when the resource is missing, refuses, or answers in another shape', async () => {
    for (const answer of [
      json(404, { error: 'RESTEASY003210: Could not find resource' }),
      json(403, {}),
      json(200, { members: ['alice'] }),
    ]) {
      answers = [json(200, { access_token: 't', expires_in: 300 }), answer];
      await assert.rejects(keycloakRoleDirectory(ISSUER).members('c', 'r', 10), RoleLookupError);
    }
  });

  it('fails when the launchpad has no client-credentials grant', async () => {
    answers = [json(401, { error: 'unauthorized_client' })];
    await assert.rejects(keycloakRoleDirectory(ISSUER).members('c', 'r', 10), RoleLookupError);
  });
});

describe('staticRoleDirectory', () => {
  it('stands in for scout-users, truncating at max', async () => {
    const roles = staticRoleDirectory({ 'app/reviewer': ['alice', 'bob', 'carol'] });
    assert.deepEqual(await roles.members('app', 'reviewer', 2), {
      usernames: ['alice', 'bob'],
      truncated: true,
    });
    assert.equal(await roles.members('other-app', 'reviewer', 2), null);
  });
});
