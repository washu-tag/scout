'use client';

import React from 'react';
import { HiArrowRight, HiChevronRight, HiOutlineExclamation } from 'react-icons/hi';
import { DEFAULT_ICON, ICONS } from '@/lib/catalog/icons';
import { ChipBadge } from '@/components/catalog/ChipBadge';
import { ChipAnchor } from '@/components/shell/ChipAnchor';
import { TONES } from '@/lib/catalog/tones';
import type { RenderChip, RenderGroup, RenderModel } from '@/lib/catalog/types';

// Tailwind classes must be enumerable at build time: column counts resolve
// through these literal maps, never string interpolation (ADR 0034).
const CARD_COLS: Record<number, string> = {
  1: '',
  2: 'sm:grid-cols-2',
  3: 'md:grid-cols-3',
  4: 'sm:grid-cols-2 lg:grid-cols-4',
};

const TILE_COLS: Record<number, string> = {
  1: 'grid-cols-1',
  2: 'grid-cols-2',
  3: 'grid-cols-2 sm:grid-cols-3',
  4: 'grid-cols-2 sm:grid-cols-4',
};

const SURFACE = 'bg-white dark:bg-slate-900 border border-slate-200 dark:border-slate-800';
const MUTED = 'text-slate-600 dark:text-slate-400';

export function ChipIcon({ chip, className }: { chip: RenderChip; className: string }) {
  if (chip.iconData) {
    // Data URIs render via <img> only — never inlined into the DOM — so
    // embedded SVG stays inert (ADR 0034).
    // eslint-disable-next-line @next/next/no-img-element
    return <img src={chip.iconData} alt="" className={`h-5 w-5 ${className}`} />;
  }
  const Icon = ICONS[chip.icon] ?? ICONS[DEFAULT_ICON];
  return <Icon className={className} />;
}

const ToneIcon = ({ chip, size }: { chip: RenderChip; size: string }) => {
  const tone = TONES[chip.tone];
  return (
    <span
      className={`flex flex-none items-center justify-center border ${size} ${tone.iconBg} ${tone.icon}`}
    >
      <ChipIcon chip={chip} className="text-lg" />
    </span>
  );
};

// Full-width groups get the large card; half-width groups a smaller one.
const ChipCard = ({ chip, large }: { chip: RenderChip; large: boolean }) => {
  const tone = TONES[chip.tone];
  return (
    <ChipAnchor
      chip={chip}
      className={`group flex items-start no-underline transition-all duration-200 hover:-translate-y-0.5 hover:shadow-md ${SURFACE} ${tone.hoverBorder} ${tone.hoverShadow} ${
        large ? 'gap-4 p-5 rounded-2xl shadow-sm' : 'gap-3 p-4 rounded-[14px]'
      }`}
    >
      <ToneIcon
        chip={chip}
        size={large ? 'w-[42px] h-[42px] rounded-xl' : 'w-9 h-9 rounded-[10px]'}
      />
      <span className="flex min-w-0 flex-col gap-1">
        <span className="flex flex-wrap items-center gap-x-2.5 gap-y-1">
          <span
            className={`inline-flex items-center gap-1.5 font-semibold tracking-tight text-slate-900 dark:text-slate-100 ${large ? 'text-lg' : 'text-[15px]'}`}
          >
            {chip.title}
            {large && (
              <HiArrowRight
                className={`text-base ${tone.cta} transition-transform duration-200 group-hover:translate-x-1`}
              />
            )}
          </span>
          <ChipBadge chip={chip} />
        </span>
        <span className={`leading-normal ${MUTED} ${large ? 'text-sm' : 'text-[13px]'}`}>
          {chip.description}
        </span>
      </span>
    </ChipAnchor>
  );
};

const ChipRow = ({ chip }: { chip: RenderChip }) => (
  <ChipAnchor
    chip={chip}
    className="group flex items-center gap-3.5 px-4 py-2.5 no-underline transition-colors duration-200 hover:bg-slate-50 dark:hover:bg-slate-800/60"
  >
    <ToneIcon chip={chip} size="w-[34px] h-[34px] rounded-[9px]" />
    <span className="flex flex-1 min-w-0 flex-col gap-0.5">
      <span className="flex flex-wrap items-center gap-x-2 gap-y-1">
        <span className="text-sm font-semibold text-slate-900 dark:text-slate-100">
          {chip.title}
        </span>
        <ChipBadge chip={chip} />
      </span>
      <span className={`text-[13px] ${MUTED}`}>{chip.description}</span>
    </span>
    <HiChevronRight
      className={`flex-none text-base ${MUTED} transition-transform duration-200 group-hover:translate-x-0.5`}
    />
  </ChipAnchor>
);

const ChipTile = ({ chip }: { chip: RenderChip }) => {
  const tone = TONES[chip.tone];
  return (
    <ChipAnchor
      chip={chip}
      title={chip.description}
      className={`flex items-center gap-2.5 px-3 py-2.5 rounded-[14px] no-underline transition-all duration-200 hover:-translate-y-0.5 hover:shadow-md ${SURFACE} ${tone.hoverBorder} ${tone.hoverShadow}`}
    >
      <ToneIcon chip={chip} size="w-[34px] h-[34px] rounded-[9px]" />
      <span className="text-[13px] font-semibold text-slate-900 dark:text-slate-100">
        {chip.title}
      </span>
      <ChipBadge chip={chip} />
    </ChipAnchor>
  );
};

const GroupSection = ({ group, half }: { group: RenderGroup; half: boolean }) => (
  <section className="flex min-w-0 flex-col gap-3">
    <div className="flex flex-wrap items-baseline justify-between gap-x-6 gap-y-2 pb-3 border-b border-slate-200 dark:border-slate-800">
      <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1">
        <h2 className="text-base font-semibold text-slate-900 dark:text-slate-100">
          {group.title}
        </h2>
        {group.description && <span className={`text-sm ${MUTED}`}>{group.description}</span>}
      </div>
      {group.footerLink && (
        <ChipAnchor
          chip={group.footerLink.chip}
          className="inline-flex items-center gap-1.5 text-sm font-medium text-indigo-700 dark:text-indigo-300 hover:text-indigo-900 dark:hover:text-indigo-200 no-underline"
        >
          {group.footerLink.text}
          <HiArrowRight />
        </ChipAnchor>
      )}
    </div>

    {group.layout === 'cards' && (
      <div
        className={`grid grid-cols-1 ${half ? 'gap-3' : 'gap-4'} ${
          (half ? CARD_COLS[Math.min(group.columns, 2)] : CARD_COLS[group.columns]) ?? ''
        }`}
      >
        {group.chips.map((chip) => (
          <ChipCard key={`${chip.source}:${chip.id}`} chip={chip} large={!half} />
        ))}
      </div>
    )}
    {group.layout === 'rows' && (
      <div
        className={`flex flex-col overflow-hidden rounded-[14px] divide-y divide-slate-200 dark:divide-slate-800 ${SURFACE}`}
      >
        {group.chips.map((chip) => (
          <ChipRow key={`${chip.source}:${chip.id}`} chip={chip} />
        ))}
      </div>
    )}
    {group.layout === 'tiles' && (
      <div
        className={`grid gap-2.5 ${TILE_COLS[half ? group.columns : Math.min(group.columns * 2, 4)] ?? 'grid-cols-2'}`}
      >
        {group.chips.map((chip) => (
          <ChipTile key={`${chip.source}:${chip.id}`} chip={chip} />
        ))}
      </div>
    )}
  </section>
);

// Admin-only: the loader and validator report everything they skipped or
// coerced here, because the person who just shipped a broken chip is looking
// at the page, not at Loki.
const DiagnosticsPanel = ({ model }: { model: RenderModel }) => {
  if (model.diagnostics.length === 0) return null;
  return (
    <div className="bg-amber-50 dark:bg-amber-950/30 border border-amber-200 dark:border-amber-900/50 rounded-2xl p-4">
      <div className="flex items-center gap-2 mb-2 text-amber-700 dark:text-amber-400">
        <HiOutlineExclamation className="text-base flex-shrink-0" />
        <span className="text-xs font-semibold uppercase tracking-[0.18em]">
          {model.diagnostics.length} catalog{' '}
          {model.diagnostics.length === 1
            ? 'entry reported a problem'
            : 'entries reported problems'}
        </span>
      </div>
      <ul className="space-y-1">
        {model.diagnostics.map((diagnostic, index) => (
          <li key={index} className="text-xs text-amber-800 dark:text-amber-300 font-light">
            <span className="font-medium">
              {diagnostic.source}
              {diagnostic.subject ? ` · ${diagnostic.subject}` : ''}
            </span>
            {': '}
            {diagnostic.message}
          </li>
        ))}
      </ul>
    </div>
  );
};

// Renders one error boundary per group, so a renderer bug costs a single
// section — and says so in place, rather than leaving a silent gap. With
// validation upstream this should never trigger; it exists for the day it
// does.
class SectionBoundary extends React.Component<
  { title: string; children: React.ReactNode },
  { failed: boolean }
> {
  constructor(props: { title: string; children: React.ReactNode }) {
    super(props);
    this.state = { failed: false };
  }

  static getDerivedStateFromError() {
    return { failed: true };
  }

  componentDidCatch(error: unknown) {
    console.error('[catalog] section failed to render', error);
  }

  render() {
    if (this.state.failed) {
      return (
        <div className={`rounded-2xl p-8 text-center ${SURFACE}`}>
          <p className={`text-sm ${MUTED}`}>
            The “{this.props.title}” section failed to render. Details are in the browser console
            and the launchpad logs.
          </p>
        </div>
      );
    }
    return this.props.children;
  }
}

export default function CatalogSections({ model }: { model: RenderModel }) {
  if (model.rows.length === 0) {
    return (
      <div className="space-y-6">
        <div className={`rounded-2xl p-8 text-center ${SURFACE}`}>
          <p className={`text-sm ${MUTED}`}>No services are registered on this launchpad yet.</p>
        </div>
        <DiagnosticsPanel model={model} />
      </div>
    );
  }

  return (
    <div className="flex flex-col gap-8 tall:gap-11">
      {model.rows.map((row) =>
        row.groups.length === 2 ? (
          <div
            key={row.groups[0].id}
            className="grid gap-8 md:grid-cols-2 tall:gap-11 tall:md:gap-10"
          >
            {row.groups.map((group) => (
              <SectionBoundary key={group.id} title={group.title}>
                <GroupSection group={group} half />
              </SectionBoundary>
            ))}
          </div>
        ) : (
          <SectionBoundary key={row.groups[0].id} title={row.groups[0].title}>
            <GroupSection group={row.groups[0]} half={false} />
          </SectionBoundary>
        ),
      )}
      <DiagnosticsPanel model={model} />
    </div>
  );
}
