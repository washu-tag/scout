'use client';

import React from 'react';
import { HiArrowRight, HiOutlineDocumentText, HiOutlineExclamation } from 'react-icons/hi';
import { DEFAULT_ICON, ICONS } from '@/lib/catalog/icons';
import { TONES } from '@/lib/catalog/tones';
import type { RenderChip, RenderGroup, RenderModel } from '@/lib/catalog/types';

// Tailwind classes must be enumerable at build time: column counts resolve
// through these literal maps, never string interpolation (ADR 0034).
const CARD_COLS: Record<number, string> = {
  1: '',
  2: 'lg:grid-cols-2',
  3: 'lg:grid-cols-3',
  4: 'lg:grid-cols-4',
};

const TILE_COLS: Record<number, string> = {
  1: 'grid-cols-1',
  2: 'grid-cols-2',
  3: 'grid-cols-3',
  4: 'grid-cols-4',
};

function ChipIcon({ chip, className }: { chip: RenderChip; className: string }) {
  if (chip.iconData) {
    // Data URIs render via <img> only — never inlined into the DOM — so
    // embedded SVG stays inert (ADR 0034).
    // eslint-disable-next-line @next/next/no-img-element
    return <img src={chip.iconData} alt="" className={`h-5 w-5 ${className}`} />;
  }
  const Icon = ICONS[chip.icon] ?? ICONS[DEFAULT_ICON];
  return <Icon className={className} />;
}

function linkTarget(chip: RenderChip) {
  return chip.newTab ? { target: '_blank', rel: 'noopener noreferrer' } : {};
}

// Large card — the Core Services idiom.
const ChipCard = ({ chip }: { chip: RenderChip }) => {
  const tone = TONES[chip.tone];
  return (
    <a
      href={chip.href}
      {...linkTarget(chip)}
      className={`group block p-6 bg-white dark:bg-slate-900 border border-slate-200 dark:border-slate-800 rounded-2xl transition-all duration-200 no-underline hover:shadow-lg hover:-translate-y-0.5 ${tone.hoverBorder} ${tone.hoverShadow}`}
    >
      <div className="flex items-center gap-3 mb-4">
        <div
          className={`w-11 h-11 rounded-xl border flex items-center justify-center transition-colors duration-200 ${tone.iconBg}`}
        >
          <div className={`text-xl ${tone.icon}`}>
            <ChipIcon chip={chip} className="text-xl" />
          </div>
        </div>
        <h3 className="text-xl font-semibold text-slate-900 dark:text-white tracking-tight">
          {chip.title}
        </h3>
      </div>
      <p className="text-base text-slate-500 dark:text-slate-400 mb-4 leading-relaxed font-light">
        {chip.description}
      </p>
      <div className={`flex items-center gap-1 font-medium text-sm ${tone.cta}`}>
        <span className="group-hover:translate-x-0.5 transition-transform duration-200">Open</span>
        <HiArrowRight className="transform group-hover:translate-x-1 transition-transform duration-200 ease-out" />
      </div>
    </a>
  );
};

// Compact chip: the rows idiom (Playbooks) carries a trailing arrow, the
// tiles idiom (Admin Tools) does not. One component so their chrome cannot
// drift apart.
const ChipCompact = ({ chip, arrow }: { chip: RenderChip; arrow: boolean }) => {
  const tone = TONES[chip.tone];
  return (
    <a
      href={chip.href}
      {...linkTarget(chip)}
      className={`group flex items-center gap-3 p-4 bg-white dark:bg-slate-900 border border-slate-200 dark:border-slate-800 rounded-xl hover:shadow-md hover:-translate-y-0.5 transition-all duration-200 no-underline ${tone.hoverBorder} ${tone.hoverShadow}`}
    >
      <div
        className={`w-10 h-10 rounded-lg border flex items-center justify-center flex-shrink-0 ${tone.iconBg}`}
      >
        <div className={`text-xl ${tone.icon}`}>
          <ChipIcon chip={chip} className="text-xl" />
        </div>
      </div>
      <div className="flex-1 min-w-0">
        <h3 className="text-sm font-semibold text-slate-900 dark:text-white tracking-tight">
          {chip.title}
        </h3>
        <p className="text-xs text-slate-500 dark:text-slate-400 leading-snug font-light">
          {chip.description}
        </p>
      </div>
      {arrow && (
        <HiArrowRight
          className={`text-base ${tone.cta} group-hover:translate-x-1 transition-transform duration-200 flex-shrink-0`}
        />
      )}
    </a>
  );
};

const GroupPanel = ({ group, fillHeight }: { group: RenderGroup; fillHeight: boolean }) => {
  const HeaderIcon = ICONS[group.icon] ?? ICONS[DEFAULT_ICON];
  return (
    <div
      className={`bg-white dark:bg-slate-900 border border-slate-200 dark:border-slate-800 rounded-3xl p-8 shadow-sm ${
        fillHeight ? 'h-full' : ''
      } ${group.layout === 'tiles' ? 'flex flex-col' : ''}`}
    >
      <div className="text-center mb-6">
        <div className="flex items-center justify-center gap-2 mb-3">
          <div className="w-7 h-7 rounded-md bg-slate-100 dark:bg-slate-800 border border-slate-200 dark:border-slate-700 flex items-center justify-center">
            <HeaderIcon className="text-sm text-slate-600 dark:text-slate-300" />
          </div>
          <h2 className="text-xs font-semibold text-slate-700 dark:text-slate-200 uppercase tracking-[0.18em]">
            {group.title}
          </h2>
        </div>
        {group.description && (
          <p className="text-sm text-slate-500 dark:text-slate-400 font-light">
            {group.description}
          </p>
        )}
      </div>

      {group.layout === 'cards' && (
        <div className={`grid grid-cols-1 gap-8 ${CARD_COLS[group.columns] ?? ''}`}>
          {group.chips.map((chip) => (
            <ChipCard key={`${chip.source}:${chip.id}`} chip={chip} />
          ))}
        </div>
      )}
      {group.layout === 'rows' && (
        <div className="space-y-3">
          {group.chips.map((chip) => (
            <ChipCompact key={`${chip.source}:${chip.id}`} chip={chip} arrow />
          ))}
        </div>
      )}
      {group.layout === 'tiles' && (
        <div className={`grid gap-3 flex-1 ${TILE_COLS[group.columns] ?? 'grid-cols-2'}`}>
          {group.chips.map((chip) => (
            <ChipCompact key={`${chip.source}:${chip.id}`} chip={chip} arrow={false} />
          ))}
        </div>
      )}

      {group.footerLink && (
        <div className="mt-6 text-center">
          <a
            href={group.footerLink.url}
            target="_blank"
            rel="noopener noreferrer"
            className="inline-flex items-center gap-2 px-4 py-2 text-sm text-slate-500 dark:text-slate-400 hover:text-slate-900 dark:hover:text-white transition-colors duration-200 no-underline"
          >
            <HiOutlineDocumentText className="text-base" />
            <span>{group.footerLink.text}</span>
            <HiArrowRight className="text-base" />
          </a>
        </div>
      )}
    </div>
  );
};

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

// Renders one error boundary per section, so a renderer bug costs a section,
// not the page. With validation upstream this should never trigger; it exists
// for the day it does.
class SectionBoundary extends React.Component<{ children: React.ReactNode }, { failed: boolean }> {
  constructor(props: { children: React.ReactNode }) {
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
    if (this.state.failed) return null;
    return this.props.children;
  }
}

export default function CatalogSections({ model }: { model: RenderModel }) {
  if (model.rows.length === 0) {
    return (
      <div className="space-y-6">
        <div className="bg-white dark:bg-slate-900 border border-slate-200 dark:border-slate-800 rounded-3xl p-8 shadow-sm text-center">
          <p className="text-sm text-slate-500 dark:text-slate-400 font-light">
            No services are registered on this launchpad yet.
          </p>
        </div>
        <DiagnosticsPanel model={model} />
      </div>
    );
  }

  return (
    <div className="space-y-6">
      {model.rows.map((row) => (
        <SectionBoundary key={row.groups.map((group) => group.id).join('+')}>
          {row.groups.length === 2 ? (
            <div className="grid gap-6 md:grid-cols-2">
              {row.groups.map((group) => (
                <GroupPanel key={group.id} group={group} fillHeight />
              ))}
            </div>
          ) : (
            <GroupPanel group={row.groups[0]} fillHeight={false} />
          )}
        </SectionBoundary>
      ))}
      <DiagnosticsPanel model={model} />
    </div>
  );
}
