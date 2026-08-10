// Generates the icon and tone galleries for the chip authoring guide
// (docs/source/customize/launchpad-chips.md, ADR 0034): one SVG per icon
// rendered from the app's own registry, one swatch per tone from the app's
// own class bundles, and markdown tables spliced between generated-content
// markers in the page — so the docs show exactly what the launchpad renders.
// Run with `npm run docs-assets`; doc-assets.test.ts fails when any committed
// artifact drifts from this output.
import { mkdirSync, readFileSync, rmSync, writeFileSync } from 'fs';
import { join, resolve } from 'path';
import { fileURLToPath } from 'url';
import { createElement } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { ICONS } from '../src/lib/catalog/icons';
import { TONES, type Tone, type ToneName } from '../src/lib/catalog/tones';

const DOCS_SOURCE = resolve(import.meta.dirname, '../../docs/source');
export const ICONS_DIR = join(DOCS_SOURCE, 'images/launchpad/icons');
export const TONES_DIR = join(DOCS_SOURCE, 'images/launchpad/tones');
export const PAGE_PATH = join(DOCS_SOURCE, 'customize/launchpad-chips.md');

// Image paths as referenced from the page (docs/source/customize/).
const ICONS_REL = '../images/launchpad/icons';
const TONES_REL = '../images/launchpad/tones';

// slate-500: legible on both light and dark documentation themes.
const ICON_COLOR = '#64748b';

// ---------------------------------------------------------------------------
// Color math: Tailwind v4 defines its palette in oklch(); SVG renderers and
// non-browser consumers are happier with hex, so convert (CSS Color 4 / Björn
// Ottosson's OKLab definition). doc-assets.test.ts pins known values.
// ---------------------------------------------------------------------------

export function oklchToHex(l: number, c: number, hDeg: number): string {
  const hRad = (hDeg * Math.PI) / 180;
  const a = c * Math.cos(hRad);
  const b = c * Math.sin(hRad);

  const lp = l + 0.3963377774 * a + 0.2158037573 * b;
  const mp = l - 0.1055613458 * a - 0.0638541728 * b;
  const sp = l - 0.0894841775 * a - 1.291485548 * b;
  const l3 = lp ** 3;
  const m3 = mp ** 3;
  const s3 = sp ** 3;

  const rLin = 4.0767416621 * l3 - 3.3077115913 * m3 + 0.2309699292 * s3;
  const gLin = -1.2684380046 * l3 + 2.6097574011 * m3 - 0.3413193965 * s3;
  const bLin = -0.0041960863 * l3 - 0.7034186147 * m3 + 1.707614701 * s3;

  const toByte = (channel: number) => {
    const clamped = Math.min(1, Math.max(0, channel));
    const srgb = clamped <= 0.0031308 ? 12.92 * clamped : 1.055 * clamped ** (1 / 2.4) - 0.055;
    return Math.round(Math.min(1, Math.max(0, srgb)) * 255);
  };
  return `#${[rLin, gLin, bLin]
    .map((channel) => toByte(channel).toString(16).padStart(2, '0'))
    .join('')}`;
}

// '<family>-<weight>' (e.g. 'indigo-600') → hex, parsed from the installed
// Tailwind package so swatches track the palette the app actually ships.
export function tailwindPalette(): Map<string, string> {
  const css = readFileSync(
    resolve(import.meta.dirname, '../node_modules/tailwindcss/theme.css'),
    'utf-8',
  );
  const palette = new Map<string, string>();
  for (const match of css.matchAll(/--color-([a-z]+-\d+):\s*oklch\(([^)]+)\)/g)) {
    const [l, c, h] = match[2].split(/\s+/).map(Number);
    palette.set(match[1], oklchToHex(l, c, h));
  }
  return palette;
}

// ---------------------------------------------------------------------------
// Tone swatches: the icon-chip treatment in both modes, extracted from the
// same class bundles the app renders with. Dark-mode tokens carry alpha
// (e.g. dark:bg-indigo-950/40 = indigo-950 at 40%), so the dark swatch
// composites them over the actual dark card surface (slate-900), exactly as
// the browser does; the light swatch sits on the white card likewise.
// ---------------------------------------------------------------------------

export type SwatchMode = 'light' | 'dark';

interface Paint {
  hex: string;
  alpha: number;
  token: string;
}

export interface ToneSwatch {
  background: Paint;
  border: Paint;
  accent: Paint;
}

function modeToken(classes: string, prefix: string, mode: SwatchMode): string {
  const wanted = mode === 'dark' ? `dark:${prefix}` : prefix;
  const token = classes
    .split(/\s+/)
    .find((cls) =>
      mode === 'dark' ? cls.startsWith(wanted) : cls.startsWith(prefix) && !cls.startsWith('dark:'),
    );
  if (!token) throw new Error(`no ${mode}-mode ${prefix}* token in "${classes}"`);
  return token;
}

function resolvePaint(token: string, palette: Map<string, string>): Paint {
  const match = token.match(/^(?:dark:)?(?:bg|border|text)-([a-z]+-\d+)(?:\/(\d+))?$/);
  if (!match) throw new Error(`unparseable color token "${token}"`);
  const hex = palette.get(match[1]);
  if (!hex) throw new Error(`Tailwind palette has no color "${match[1]}" (from ${token})`);
  return { hex, alpha: match[2] ? Number(match[2]) / 100 : 1, token };
}

export function toneSwatch(tone: Tone, palette: Map<string, string>, mode: SwatchMode): ToneSwatch {
  return {
    background: resolvePaint(modeToken(tone.iconBg, 'bg-', mode), palette),
    border: resolvePaint(modeToken(tone.iconBg, 'border-', mode), palette),
    accent: resolvePaint(modeToken(tone.icon, 'text-', mode), palette),
  };
}

export function toneSvg(swatch: ToneSwatch, surface: { fill: string; stroke: string }): string {
  return [
    '<svg xmlns="http://www.w3.org/2000/svg" width="36" height="36" viewBox="0 0 36 36">',
    `  <rect x="1" y="1" width="34" height="34" rx="8" fill="${surface.fill}" stroke="${surface.stroke}" stroke-width="1"/>`,
    `  <rect x="7" y="7" width="22" height="22" rx="7" fill="${swatch.background.hex}" fill-opacity="${swatch.background.alpha}" stroke="${swatch.border.hex}" stroke-opacity="${swatch.border.alpha}" stroke-width="1.5"/>`,
    `  <circle cx="18" cy="18" r="5.5" fill="${swatch.accent.hex}" fill-opacity="${swatch.accent.alpha}"/>`,
    '</svg>',
    '',
  ].join('\n');
}

// The card surfaces the swatches sit on: bg-white / dark:bg-slate-900, with a
// hairline so each swatch stays visible on either docs theme.
function surfaces(
  palette: Map<string, string>,
): Record<SwatchMode, { fill: string; stroke: string }> {
  const slate = (weight: number) => {
    const hex = palette.get(`slate-${weight}`);
    if (!hex) throw new Error(`Tailwind palette has no color "slate-${weight}"`);
    return hex;
  };
  return {
    light: { fill: '#ffffff', stroke: slate(200) },
    dark: { fill: slate(900), stroke: slate(700) },
  };
}

export function toneSvgs(): Map<string, string> {
  const palette = tailwindPalette();
  const surface = surfaces(palette);
  const entries: Array<[string, string]> = [];
  for (const name of Object.keys(TONES) as ToneName[]) {
    entries.push([name, toneSvg(toneSwatch(TONES[name], palette, 'light'), surface.light)]);
    entries.push([`${name}-dark`, toneSvg(toneSwatch(TONES[name], palette, 'dark'), surface.dark)]);
  }
  return new Map(entries);
}

// ---------------------------------------------------------------------------
// Icon SVGs, rendered from the registry components themselves.
// ---------------------------------------------------------------------------

export function iconSvgs(): Map<string, string> {
  return new Map(
    Object.keys(ICONS)
      .sort()
      .map((name) => [
        name,
        `${renderToStaticMarkup(createElement(ICONS[name], { size: 20, color: ICON_COLOR }))}\n`,
      ]),
  );
}

// ---------------------------------------------------------------------------
// Markdown tables
// ---------------------------------------------------------------------------

const ICON_PAIRS_PER_ROW = 3;

export function iconsTable(): string {
  const names = Object.keys(ICONS).sort();
  const header = Array(ICON_PAIRS_PER_ROW).fill('Icon | Name').join(' | ');
  const divider = Array(ICON_PAIRS_PER_ROW).fill('--- | ---').join(' | ');
  const rows: string[] = [];
  for (let i = 0; i < names.length; i += ICON_PAIRS_PER_ROW) {
    const cells = Array.from({ length: ICON_PAIRS_PER_ROW }, (_, offset) => {
      const name = names[i + offset];
      return name ? `![${name}](${ICONS_REL}/${name}.svg) | \`${name}\`` : ' | ';
    });
    rows.push(`| ${cells.join(' | ')} |`);
  }
  return [`| ${header} |`, `| ${divider} |`, ...rows, ''].join('\n');
}

export function tonesTable(): string {
  const palette = tailwindPalette();
  const rows = (Object.keys(TONES) as ToneName[]).map((name) => {
    const light = toneSwatch(TONES[name], palette, 'light');
    const dark = toneSwatch(TONES[name], palette, 'dark');
    return (
      `| ![${name} light](${TONES_REL}/${name}.svg) | ![${name} dark](${TONES_REL}/${name}-dark.svg) ` +
      `| \`${name}\` | \`${light.background.token}\` \`${dark.background.token}\` ` +
      `| \`${light.accent.token}\` \`${dark.accent.token}\` |`
    );
  });
  return [
    '| Light | Dark | Name | Background | Icon color |',
    '| --- | --- | --- | --- | --- |',
    ...rows,
    '',
  ].join('\n');
}

// ---------------------------------------------------------------------------
// Page splicing
// ---------------------------------------------------------------------------

function escapeRegExp(text: string): string {
  return text.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
}

export function spliceGenerated(page: string, marker: string, content: string): string {
  const start = `<!-- generated:${marker}:start — npm run docs-assets in launchpad/ -->`;
  const end = `<!-- generated:${marker}:end -->`;
  const pattern = new RegExp(`${escapeRegExp(start)}[\\s\\S]*?${escapeRegExp(end)}`);
  if (!pattern.test(page)) {
    throw new Error(`generated-content markers for "${marker}" not found in page`);
  }
  return page.replace(pattern, `${start}\n\n${content}\n${end}`);
}

export function generatePage(page: string): string {
  return spliceGenerated(spliceGenerated(page, 'icons', iconsTable()), 'tones', tonesTable());
}

// ---------------------------------------------------------------------------

function main(): void {
  for (const [dir, files] of [
    [ICONS_DIR, iconSvgs()],
    [TONES_DIR, toneSvgs()],
  ] as const) {
    rmSync(dir, { recursive: true, force: true });
    mkdirSync(dir, { recursive: true });
    for (const [name, svg] of files) {
      writeFileSync(join(dir, `${name}.svg`), svg);
    }
    console.log(`wrote ${files.size} SVGs to ${dir}`);
  }
  writeFileSync(PAGE_PATH, generatePage(readFileSync(PAGE_PATH, 'utf-8')));
  console.log(`spliced generated tables into ${PAGE_PATH}`);
}

if (process.argv[1] && resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  main();
}
