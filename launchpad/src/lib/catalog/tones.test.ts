import assert from 'node:assert/strict';
import { describe, it } from 'node:test';
import { tailwindPalette } from '../../../scripts/generate-doc-assets';
import { TONES } from './tones';

type Rgb = [number, number, number];

const palette = tailwindPalette();

function color(classes: string, prefix: string, dark: boolean): { rgb: Rgb; alpha: number } {
  const token = classes
    .split(/\s+/)
    .find((cls) => (dark ? cls.startsWith(`dark:${prefix}`) : cls.startsWith(prefix)));
  const match = token?.match(/-([a-z]+-\d+)(?:\/(\d+))?$/);
  const hex = match && palette.get(match[1]);
  assert.ok(hex, `no ${dark ? 'dark' : 'light'} ${prefix} color in "${classes}"`);
  const rgb = [1, 3, 5].map((i) => parseInt(hex.slice(i, i + 2), 16)) as Rgb;
  return { rgb, alpha: match[2] ? Number(match[2]) / 100 : 1 };
}

function luminance(rgb: Rgb): number {
  const [r, g, b] = rgb.map((channel) => {
    const s = channel / 255;
    return s <= 0.04045 ? s / 12.92 : ((s + 0.055) / 1.055) ** 2.4;
  });
  return 0.2126 * r + 0.7152 * g + 0.0722 * b;
}

function contrast(a: Rgb, b: Rgb): number {
  const [hi, lo] = [luminance(a), luminance(b)].sort((x, y) => y - x);
  return (hi + 0.05) / (lo + 0.05);
}

// Badge text is 12px, so WCAG AA asks for 4.5:1. Dark backgrounds carry
// alpha over the slate-900 card.
describe('badge tones', () => {
  const card = color('dark:bg-slate-900', 'bg-', true).rgb;
  for (const [name, tone] of Object.entries(TONES)) {
    for (const dark of [false, true]) {
      it(`${name} ${dark ? 'dark' : 'light'} badge text reaches 4.5:1`, () => {
        const bg = color(tone.iconBg, 'bg-', dark);
        const surface = bg.rgb.map((c, i) => c * bg.alpha + card[i] * (1 - bg.alpha)) as Rgb;
        const ratio = contrast(color(tone.badgeText, 'text-', dark).rgb, surface);
        assert.ok(ratio >= 4.5, `${ratio.toFixed(2)}:1`);
      });
    }
  }
});
