// Vega-Lite `config` for charts the LLM writes via scout_chart_sql.
//
// Merged at render, never stored: a chart saved in light mode has to redraw
// correctly in dark mode, and editing this file has to improve charts that
// were saved months ago. The stored spec stays semantic (mark + encoding).
//
// Chrome and ink mirror theme.css so charts sit in the viewer rather than on
// top of it. Vega needs literal colors, not var(), so the token values are
// duplicated here; keep the two in sync.

type Ink = {
  surface: string;
  fg: string;
  muted: string;
  border: string;
  grid: string;
};

const LIGHT_INK: Ink = {
  surface: '#fff',
  fg: '#222',
  muted: '#666',
  border: '#e2e2e2',
  grid: '#ececec',
};

const DARK_INK: Ink = {
  surface: '#242424',
  fg: '#e6e6e6',
  muted: '#9a9a9a',
  border: '#3a3a3a',
  grid: '#333',
};

// Six hues drawn from the launchpad's chip tones (launchpad/src/lib/catalog/
// tones.ts, ADR 0034) so charts speak the same color language as the rest of
// Scout. Their Tailwind steps are picked per mode, not per brand: -600 sits in
// the valid lightness band against both #fff and #242424, so orange, emerald
// and cyan are mode-invariant, while indigo, violet and rose lighten to -500
// on the dark surface.
//
// Fixed slot order, never cycled. The ordering IS the colorblind-safety
// mechanism, not decoration. Validated against both real surfaces: worst
// adjacent CVD deltaE 10.1 in both modes, normal-vision 24.5 light / 21.1
// dark, every slot >= 3:1 contrast. A 7th series is never a generated hue, it
// folds into Other or the chart facets.
//
// Two of Flavin's nine tones are deliberately absent: amber collides with
// orange and red collides with rose, and no ordering of all eight clears the
// adjacency floors. Slate is chrome, not a series color. Re-run the dataviz
// validator against #fff and #242424 before changing any of this.
const CATEGORY_LIGHT = [
  '#4f46e5', // indigo-600
  '#ea580c', // orange-600
  '#059669', // emerald-600
  '#7c3aed', // violet-600
  '#0891b2', // cyan-600
  '#e11d48', // rose-600
];

const CATEGORY_DARK = [
  '#6366f1', // indigo-500
  '#ea580c', // orange-600
  '#059669', // emerald-600
  '#8b5cf6', // violet-500
  '#0891b2', // cyan-600
  '#f43f5e', // rose-500
];

// Single hue, light to dark, for continuous magnitude. Indigo, matching the
// first categorical slot. Reversed on dark so "more" still reads as the step
// furthest from the surface.
const RAMP_LIGHT = ['#e0e7ff', '#c7d2fe', '#a5b4fc', '#818cf8', '#6366f1', '#4f46e5', '#4338ca'];
const RAMP_DARK = ['#312e81', '#3730a3', '#4338ca', '#4f46e5', '#6366f1', '#818cf8', '#a5b4fc'];

const FONT = 'system-ui, -apple-system, "Segoe UI", sans-serif';

function build(ink: Ink, category: string[], ramp: string[]) {
  return {
    background: ink.surface,
    font: FONT,
    padding: 8,
    view: {
      // Vega-Lite draws a border box around the plot by default.
      stroke: null,
    },
    range: {
      category,
      ramp,
      heatmap: ramp,
    },
    axis: {
      labelColor: ink.muted,
      labelFontSize: 11,
      titleColor: ink.fg,
      titleFontSize: 12,
      titleFontWeight: 600,
      titlePadding: 8,
      labelPadding: 4,
      domainColor: ink.border,
      tickColor: ink.border,
      tickSize: 4,
      gridColor: ink.grid,
      gridWidth: 1,
      labelOverlap: 'greedy',
      // Diagnosis names are long; the 180px default truncates most of them.
      labelLimit: 260,
    },
    // Grid on the measure axis only, keyed off scale type rather than axis
    // position so horizontal bars get vertical rules and vertical bars get
    // horizontal ones.
    axisQuantitative: { grid: true, domain: false, ticks: false },
    axisDiscrete: { grid: false, domain: true, ticks: true },
    legend: {
      // Horizontal, above the plot. A right-side legend competes with
      // width:'container' for horizontal space and overflows the frame.
      // Always present for 2+ series so identity is never color alone.
      orient: 'top',
      direction: 'horizontal',
      title: null,
      labelColor: ink.fg,
      labelFontSize: 11,
      symbolType: 'square',
      symbolSize: 80,
      offset: 4,
    },
    title: {
      color: ink.fg,
      fontSize: 13,
      fontWeight: 600,
      anchor: 'start',
      offset: 10,
      subtitleColor: ink.muted,
    },
    // Thin marks, rounded data-end anchored to the baseline.
    bar: { cornerRadiusEnd: 3 },
    line: { strokeWidth: 2 },
    point: { filled: true, size: 70 },
    area: { opacity: 0.85, line: { strokeWidth: 2 } },
    rule: { color: ink.muted },
    text: { color: ink.fg, fontSize: 11 },
    // Hover is the default, not an opt-in the model has to remember.
    mark: { tooltip: true },
    numberFormat: ',.4~f',
  };
}

export const CHART_THEME_LIGHT = build(LIGHT_INK, CATEGORY_LIGHT, RAMP_LIGHT);
export const CHART_THEME_DARK = build(DARK_INK, CATEGORY_DARK, RAMP_DARK);

export function chartTheme(dark: boolean) {
  return dark ? CHART_THEME_DARK : CHART_THEME_LIGHT;
}
