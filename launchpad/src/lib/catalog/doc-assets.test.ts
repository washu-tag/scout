import assert from 'node:assert/strict';
import { describe, it } from 'node:test';
import { readdirSync, readFileSync } from 'fs';
import { join } from 'path';
import {
  ICONS_DIR,
  PAGE_PATH,
  TONES_DIR,
  generatePage,
  iconSvgs,
  oklchToHex,
  toneSvgs,
} from '../../../scripts/generate-doc-assets';

// The icon/tone galleries in the docs are generated from the app's own
// registries — these tests are the drift guard. If they fail, run
// `npm run docs-assets` and commit the result.

function channels(hex: string): number[] {
  return [hex.slice(1, 3), hex.slice(3, 5), hex.slice(5, 7)].map((c) => parseInt(c, 16));
}

function assertClose(actual: string, expected: string, tolerance: number): void {
  const a = channels(actual);
  const e = channels(expected);
  for (let i = 0; i < 3; i += 1) {
    const delta = Math.abs(a[i] - e[i]);
    assert.ok(
      delta <= tolerance,
      `${actual} vs ${expected} channel ${i}: off by ${delta} (tolerance ${tolerance})`,
    );
  }
}

describe('oklchToHex', () => {
  it('pins achromatic anchors', () => {
    assert.strictEqual(oklchToHex(1, 0, 0), '#ffffff');
    assert.strictEqual(oklchToHex(0, 0, 0), '#000000');
    const gray = channels(oklchToHex(0.5, 0, 0));
    assert.strictEqual(gray[0], gray[1]);
    assert.strictEqual(gray[1], gray[2]);
    assert.ok(gray[0] >= 0x60, `mid gray ${gray[0]} below 0x60`);
    assert.ok(gray[0] <= 0x66, `mid gray ${gray[0]} above 0x66`);
  });

  it('round-trips sRGB red through its published OKLCH coordinates', () => {
    assertClose(oklchToHex(0.627955, 0.257683, 29.2338), '#ff0000', 2);
  });
});

function assertDirMatches(dir: string, expected: Map<string, string>): void {
  const files = readdirSync(dir).sort();
  assert.deepStrictEqual(files, [...expected.keys()].map((name) => `${name}.svg`).sort());
  for (const [name, svg] of expected) {
    assert.strictEqual(readFileSync(join(dir, `${name}.svg`), 'utf-8'), svg, name);
  }
}

describe('committed doc assets', () => {
  it('icon SVGs match the registry', () => {
    assertDirMatches(ICONS_DIR, iconSvgs());
  });

  it('tone swatches match the tone bundles and installed palette', () => {
    assertDirMatches(TONES_DIR, toneSvgs());
  });

  it('the spliced page tables are current (regeneration is a no-op)', () => {
    const page = readFileSync(PAGE_PATH, 'utf-8');
    assert.strictEqual(generatePage(page), page);
  });
});
