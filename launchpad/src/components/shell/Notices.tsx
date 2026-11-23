'use client';

import React, { useCallback, useEffect, useRef, useState } from 'react';
import { HiOutlineArchive, HiOutlineBell } from 'react-icons/hi';
import { reconnectDelay } from '@/lib/notifications/reconnect';
import type { ListedNotice, Summary } from '@/lib/notifications/store';
import { subdomainUrl } from '@/lib/subdomainUrl';

const PANEL =
  'fixed bottom-3 left-[76px] z-30 flex max-h-[min(36rem,calc(100vh-1.5rem))] w-96 max-w-[calc(100vw-88px)] flex-col rounded-xl border border-slate-200 bg-white shadow-lg dark:border-slate-700 dark:bg-slate-900';
const NOTE = 'px-2.5 py-6 text-center text-sm text-slate-500 dark:text-slate-400';

// The reader's counts, kept current over the notices stream (ADR 0043). The
// shell's session check runs when the stream says the session ended, and
// before each reconnect.
export function useNoticeSummary(enabled: boolean, checkSession: () => void) {
  const [summary, setSummary] = useState<Summary | null>(null);
  const check = useRef(checkSession);
  check.current = checkSession;

  useEffect(() => {
    if (!enabled) return;
    let source: EventSource | null = null;
    let timer: number | undefined;
    let attempt = 0;
    const retry = () => {
      source?.close();
      if (timer !== undefined) return;
      timer = window.setTimeout(() => {
        timer = undefined;
        check.current();
        connect();
      }, reconnectDelay(attempt++));
    };
    const connect = () => {
      source = new EventSource('/api/notifications/stream');
      source.addEventListener('counts', (event) => {
        attempt = 0;
        setSummary(JSON.parse((event as MessageEvent<string>).data));
      });
      source.addEventListener('session', () => {
        source?.close();
        check.current();
        retry();
      });
      source.onerror = retry;
    };
    connect();
    return () => {
      source?.close();
      window.clearTimeout(timer);
    };
  }, [enabled]);

  return [summary, setSummary] as const;
}

export function NoticeBell({ unseen }: { unseen: number }) {
  return (
    <span className="relative flex">
      <HiOutlineBell />
      {unseen > 0 && (
        <span className="absolute -right-2 -top-1.5 flex h-4 min-w-4 items-center justify-center rounded-full bg-rose-600 px-1 text-[10px] font-semibold leading-none text-white ring-2 ring-white dark:bg-rose-500 dark:ring-slate-900">
          {unseen > 99 ? '99+' : unseen}
        </span>
      )}
    </span>
  );
}

const UNITS: [Intl.RelativeTimeFormatUnit, number][] = [
  ['year', 31_536_000],
  ['month', 2_592_000],
  ['week', 604_800],
  ['day', 86_400],
  ['hour', 3_600],
  ['minute', 60],
];

function ago(iso: string): string {
  const seconds = (new Date(iso).getTime() - Date.now()) / 1000;
  const format = new Intl.RelativeTimeFormat('en', { numeric: 'auto', style: 'short' });
  for (const [unit, size] of UNITS) {
    if (Math.abs(seconds) >= size) return format.format(Math.round(seconds / size), unit);
  }
  return 'just now';
}

interface PanelProps {
  summary: Summary | null;
  onSummary: (summary: Summary) => void;
  // Opens a Scout page inside the shell; false when no app owns it.
  onOpen: (href: string) => boolean;
  onClose: () => void;
}

// Everything here is plain text from the sending app, rendered as text.
export function NoticesPanel({ summary, onSummary, onOpen, onClose }: PanelProps) {
  const [notices, setNotices] = useState<ListedNotice[] | null>(null);
  const [failed, setFailed] = useState(false);
  const marker = summary?.marker;

  const post = useCallback(
    async (action: 'seen' | 'read' | 'archive', body: { ids: string[] } | { all: true }) => {
      try {
        const response = await fetch(`/api/notifications/${action}`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(body),
        });
        if (response.ok) onSummary(await response.json());
      } catch {
        // The stream brings the counts back in step.
      }
    },
    [onSummary],
  );

  // Whatever the panel shows has been seen.
  useEffect(() => {
    let cancelled = false;
    fetch('/api/notifications', { cache: 'no-store' })
      .then((response) => (response.ok ? response.json() : Promise.reject()))
      .then((data: { notices: ListedNotice[] }) => {
        if (cancelled) return;
        setNotices(data.notices);
        setFailed(false);
        const unseen = data.notices.filter((notice) => !notice.seen).map((notice) => notice.id);
        if (unseen.length > 0) post('seen', { ids: unseen });
      })
      .catch(() => {
        if (!cancelled) setFailed(true);
      });
    return () => {
      cancelled = true;
    };
  }, [marker, post]);

  const update = (ids: string[] | 'all', change: Partial<ListedNotice> | null) =>
    setNotices((current) =>
      (current ?? []).flatMap((notice) =>
        ids === 'all' || ids.includes(notice.id)
          ? change
            ? [{ ...notice, ...change }]
            : []
          : [notice],
      ),
    );

  const open = (notice: ListedNotice, href: string, fallback: string) => {
    if (!notice.read) {
      update([notice.id], { read: true });
      post('read', { ids: [notice.id] });
    }
    onClose();
    if (!onOpen(href)) window.location.assign(fallback);
  };

  return (
    <div role="dialog" aria-label="Notices" className={PANEL}>
      <div className="flex items-center justify-between border-b border-slate-200 px-3 py-2 dark:border-slate-700">
        <h2 className="text-sm font-semibold text-slate-900 dark:text-white">Notices</h2>
        <button
          type="button"
          disabled={!summary?.unread}
          onClick={() => {
            update('all', { read: true });
            post('read', { all: true });
          }}
          className="rounded-md px-1.5 py-0.5 text-xs font-medium text-indigo-600 hover:bg-slate-100 disabled:cursor-default disabled:text-slate-400 disabled:hover:bg-transparent dark:text-indigo-400 dark:hover:bg-slate-800 dark:disabled:text-slate-500 cursor-pointer"
        >
          Mark all read
        </button>
      </div>
      <div className="overflow-y-auto p-1.5">
        {notices === null && (
          <p className={NOTE}>{failed ? 'Could not load notices.' : 'Loading…'}</p>
        )}
        {notices?.length === 0 && <p className={NOTE}>No notices.</p>}
        {notices && notices.length > 0 && (
          <ul>
            {notices.map((notice) => {
              const href = subdomainUrl(notice.link.subdomain, notice.link.path);
              const url = new URL(href);
              const fallback = `/open/${url.host}${url.pathname}${url.search}${url.hash}`;
              return (
                <li key={notice.id} className="group relative">
                  <a
                    href={fallback}
                    onClick={(event) => {
                      if (event.metaKey || event.ctrlKey || event.shiftKey || event.button !== 0) {
                        return;
                      }
                      event.preventDefault();
                      open(notice, href, fallback);
                    }}
                    className="flex gap-2.5 rounded-lg px-2.5 py-2 pr-9 no-underline hover:bg-slate-100 dark:hover:bg-slate-800"
                  >
                    <span
                      aria-hidden="true"
                      className={`mt-1.5 h-2 w-2 flex-shrink-0 rounded-full ${
                        notice.read ? '' : 'bg-indigo-500 dark:bg-indigo-400'
                      }`}
                    />
                    <span className="min-w-0 flex-1">
                      <span className="flex items-baseline gap-2 text-xs text-slate-500 dark:text-slate-400">
                        <span className="truncate">{notice.sender}</span>
                        <time
                          dateTime={notice.createdAt}
                          title={new Date(notice.createdAt).toLocaleString()}
                          className="flex-shrink-0"
                        >
                          {ago(notice.createdAt)}
                        </time>
                      </span>
                      <span
                        className={`block break-words text-sm ${
                          notice.read
                            ? 'text-slate-600 dark:text-slate-300'
                            : 'font-semibold text-slate-900 dark:text-white'
                        }`}
                      >
                        {!notice.read && <span className="sr-only">Unread: </span>}
                        {notice.title}
                      </span>
                      {notice.detail && (
                        <span className="mt-0.5 line-clamp-2 block break-words text-xs text-slate-500 dark:text-slate-400">
                          {notice.detail}
                        </span>
                      )}
                    </span>
                  </a>
                  <button
                    type="button"
                    title="Archive"
                    aria-label={`Archive ${notice.title}`}
                    onClick={() => {
                      update([notice.id], null);
                      post('archive', { ids: [notice.id] });
                    }}
                    className="absolute right-1.5 top-2 rounded-md p-1 text-base text-slate-400 opacity-0 transition-opacity hover:bg-slate-200 hover:text-slate-700 focus:opacity-100 group-hover:opacity-100 dark:text-slate-500 dark:hover:bg-slate-700 dark:hover:text-slate-200 cursor-pointer"
                  >
                    <HiOutlineArchive />
                  </button>
                </li>
              );
            })}
          </ul>
        )}
      </div>
    </div>
  );
}
