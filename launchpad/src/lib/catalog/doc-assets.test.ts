import { readdirSync, readFileSync } from 'fs';
import { join } from 'path';
import { describe, expect, it } from 'vitest';
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

function expectClose(actual: string, expected: string, tolerance: number): void {
  const a = channels(actual);
  const e = channels(expected);
  for (let i = 0; i < 3; i += 1) {
    expect(Math.abs(a[i] - e[i]), `${actual} vs ${expected} channel ${i}`).toBeLessThanOrEqual(
      tolerance,
    );
  }
}

describe('oklchToHex', () => {
  it('pins achromatic anchors', () => {
    expect(oklchToHex(1, 0, 0)).toBe('#ffffff');
    expect(oklchToHex(0, 0, 0)).toBe('#000000');
    const gray = channels(oklchToHex(0.5, 0, 0));
    expect(gray[0]).toBe(gray[1]);
    expect(gray[1]).toBe(gray[2]);
    expect(gray[0]).toBeGreaterThanOrEqual(0x60);
    expect(gray[0]).toBeLessThanOrEqual(0x66);
  });

  it('round-trips sRGB red through its published OKLCH coordinates', () => {
    expectClose(oklchToHex(0.627955, 0.257683, 29.2338), '#ff0000', 2);
  });
});

function assertDirMatches(dir: string, expected: Map<string, string>): void {
  const files = readdirSync(dir).sort();
  expect(files).toEqual([...expected.keys()].map((name) => `${name}.svg`).sort());
  for (const [name, svg] of expected) {
    expect(readFileSync(join(dir, `${name}.svg`), 'utf-8'), name).toBe(svg);
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
    expect(generatePage(page)).toBe(page);
  });
});
