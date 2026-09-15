import { useEffect, useRef, useState } from 'react';
import type { QueryProgress } from './api/client';

const POLL_MS = 3000;
const TICK_MS = 250;
// A faster query finishes before the indicator is worth showing.
const SHOW_AFTER_MS = 1000;
// The total wait is what users report back, so hold it before fading.
const LINGER_MS = 4000;
const FADE_MS = 600;

export interface LoadingProgress {
  show: boolean;
  done: boolean;
  progress: QueryProgress | null;
  seconds: number;
}

// Counts from when `loading` began, not `show`, so it is the whole wait.
export function useLoadingProgress(
  loading: boolean,
  fetchProgress: () => Promise<QueryProgress>,
): LoadingProgress {
  const [show, setShow] = useState(false);
  const [done, setDone] = useState(false);
  const [seconds, setSeconds] = useState(0);
  const [progress, setProgress] = useState<QueryProgress | null>(null);
  const wasShown = useRef(false);

  useEffect(() => {
    if (loading) {
      const startedAt = Date.now();
      setDone(false);
      setProgress(null);
      const showTimer = setTimeout(() => {
        wasShown.current = true;
        setShow(true);
      }, SHOW_AFTER_MS);
      const tick = setInterval(
        () => setSeconds(Math.floor((Date.now() - startedAt) / 1000)),
        TICK_MS,
      );
      return () => {
        clearTimeout(showTimer);
        clearInterval(tick);
      };
    }
    // Nothing was shown, so there is nothing to finish.
    if (!wasShown.current) {
      setShow(false);
      return;
    }
    setDone(true);
    const hide = setTimeout(() => {
      wasShown.current = false;
      setShow(false);
      setDone(false);
    }, LINGER_MS);
    return () => clearTimeout(hide);
  }, [loading]);

  // An empty or failed poll keeps the last value rather than resetting.
  useEffect(() => {
    if (!show || done) return;
    let cancelled = false;
    const poll = async () => {
      try {
        const p = await fetchProgress();
        if (!cancelled && Object.keys(p).length) setProgress(p);
      } catch {
        /* keep the last value */
      }
    };
    poll();
    const id = setInterval(poll, POLL_MS);
    return () => {
      cancelled = true;
      clearInterval(id);
    };
  }, [show, done, fetchProgress]);

  return { show, done, progress, seconds };
}

function compactNumber(n: number): string {
  if (n >= 1e9) return `${(n / 1e9).toFixed(1)}B`;
  if (n >= 1e6) return `${(n / 1e6).toFixed(1)}M`;
  if (n >= 1e3) return `${(n / 1e3).toFixed(0)}K`;
  return String(n);
}

function compactBytes(b: number): string {
  if (b >= 1e9) return `${(b / 1e9).toFixed(1)} GB`;
  if (b >= 1e6) return `${(b / 1e6).toFixed(0)} MB`;
  return `${(b / 1e3).toFixed(0)} KB`;
}

const STATE_LABELS: Record<string, string> = {
  QUEUED: 'Waiting for cluster capacity',
  PLANNING: 'Planning the query',
  STARTING: 'Starting up',
  RUNNING: 'Scanning reports',
  FINISHING: 'Fetching results',
  FINISHED: 'Fetching results',
};

function stateLine(p: QueryProgress | null): string {
  if (!p) return 'Starting up';
  if (p.queued) return STATE_LABELS.QUEUED;
  return (p.state && STATE_LABELS[p.state]) || 'Working';
}

function detailLine(p: QueryProgress | null, waitedSeconds: number): string {
  const parts: string[] = [];
  if (p?.processedRows) parts.push(`${compactNumber(p.processedRows)} rows`);
  if (p?.processedBytes) parts.push(compactBytes(p.processedBytes));
  parts.push(`${waitedSeconds}s`);
  return parts.join(' · ');
}

export function QueryProgressInline({
  progress,
  seconds,
  done,
  doneLabel,
}: LoadingProgress & { doneLabel: string }) {
  return (
    <span
      style={{
        fontSize: '0.7rem',
        color: 'var(--rv-muted)',
        fontVariantNumeric: 'tabular-nums',
        opacity: done ? 0 : 1,
        transition: `opacity ${FADE_MS}ms ease-out ${done ? LINGER_MS - FADE_MS : 0}ms`,
      }}
    >
      {done
        ? `${doneLabel} · ${seconds}s`
        : `${stateLine(progress)} · ${detailLine(progress, seconds)}`}
    </span>
  );
}

export function LoadingSpinner({ show, minHeight }: { show: boolean; minHeight: number }) {
  if (!show) return null;
  return (
    <div
      style={{
        minHeight,
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
      }}
    >
      <span
        aria-label="Loading"
        role="status"
        style={{
          width: 36,
          height: 36,
          borderRadius: '50%',
          border: '3px solid var(--rv-surface-2)',
          borderTopColor: 'var(--rv-accent)',
          animation: 'rvSpin 0.8s linear infinite',
        }}
      />
    </div>
  );
}
