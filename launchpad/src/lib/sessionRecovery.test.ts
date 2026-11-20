import assert from 'node:assert/strict';
import { describe, it } from 'node:test';
import { edgeSessionGone, sessionGate, sessionRecovery } from './sessionRecovery';

const answer = (status: number, type = 'basic') =>
  ({ status, type }) as Pick<Response, 'status' | 'type'>;

describe('sessionRecovery', () => {
  it("reloads to oauth2-proxy's sign-in page, which starts nothing until clicked", () => {
    assert.equal(sessionRecovery(answer(401)), 'reload');
  });

  it('asks before a load balancer sign-in, whose attempt expires if left waiting', () => {
    assert.equal(sessionRecovery(answer(0, 'opaqueredirect')), 'ask');
  });

  it("asks before the launchpad's own sign-in", () => {
    assert.equal(sessionRecovery(answer(440)), 'ask');
  });

  it('leaves a live session alone', () => {
    assert.equal(sessionRecovery(answer(200)), 'none');
    assert.equal(sessionRecovery(answer(500)), 'none');
  });

  it('tells the edge sign-in from the launchpad one', () => {
    assert.equal(edgeSessionGone(answer(0, 'opaqueredirect')), true);
    assert.equal(edgeSessionGone(answer(401)), true);
    assert.equal(edgeSessionGone(answer(440)), false);
  });
});

describe('sessionGate', () => {
  it('waits for the first session, then signs in if there is none', () => {
    assert.equal(sessionGate('loading', false, false), 'wait');
    assert.equal(sessionGate('unauthenticated', false, false), 'sign-in');
    assert.equal(sessionGate('authenticated', true, false), 'shell');
  });

  it('keeps the shell and its apps when next-auth loses a session it had, and checks /api/session', () => {
    assert.equal(sessionGate('unauthenticated', false, true), 'verify');
    assert.equal(sessionGate('loading', false, true), 'verify');
  });

  it('asks to sign in when /api/session then says the session ended', () => {
    assert.equal(sessionGate('unauthenticated', false, true), 'verify');
    assert.equal(sessionRecovery(answer(440)), 'ask');
    assert.equal(sessionRecovery(answer(204)), 'none');
  });
});
