import assert from 'node:assert/strict';
import { describe, it } from 'node:test';
import { reconnectDelay } from './reconnect';

const lowest = () => 0;
const highest = () => 1;

describe('reconnectDelay', () => {
  it('spreads the first reconnect over 2.5-5 seconds', () => {
    assert.equal(reconnectDelay(0, lowest), 2_500);
    assert.equal(reconnectDelay(0, highest), 5_000);
  });

  it('doubles the window after each failure, up to a minute', () => {
    assert.deepEqual(
      [1, 2, 3, 4, 10].map((attempt) => reconnectDelay(attempt, highest)),
      [10_000, 20_000, 40_000, 60_000, 60_000],
    );
    assert.equal(reconnectDelay(10, lowest), 30_000);
  });
});
