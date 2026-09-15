import { useEffect, useState } from 'react';
import type { QueryProgress } from './api/client';

const POLL_MS = 3000;
const TICK_MS = 250;
// A faster query finishes before the indicator is worth showing.
const SHOW_AFTER_MS = 1000;

export interface LoadingProgress {
  show: boolean;
  progress: QueryProgress | null;
  seconds: number;
}

// Counts from when `loading` began, not `show`, so it is the whole wait.
export function useLoadingProgress(
  loading: boolean,
  fetchProgress: () => Promise<QueryProgress>,
): LoadingProgress {
  const [show, setShow] = useState(false);
  const [seconds, setSeconds] = useState(0);
  const [progress, setProgress] = useState<QueryProgress | null>(null);

  useEffect(() => {
    if (!loading) {
      setShow(false);
      setSeconds(0);
      setProgress(null);
      return;
    }
    const startedAt = Date.now();
    const showTimer = setTimeout(() => setShow(true), SHOW_AFTER_MS);
    const tick = setInterval(
      () => setSeconds(Math.floor((Date.now() - startedAt) / 1000)),
      TICK_MS,
    );
    return () => {
      clearTimeout(showTimer);
      clearInterval(tick);
    };
  }, [loading]);

  // An empty or failed poll keeps the last value rather than resetting.
  useEffect(() => {
    if (!show) return;
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
  }, [show, fetchProgress]);

  return { show, progress, seconds };
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

export function QueryProgressInline({ progress, seconds }: LoadingProgress) {
  return (
    <span
      style={{
        fontSize: '0.7rem',
        color: 'var(--rv-muted)',
        fontVariantNumeric: 'tabular-nums',
      }}
    >
      {stateLine(progress)} · {detailLine(progress, seconds)}
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
          width: 28,
          height: 28,
          borderRadius: '50%',
          border: '2px solid var(--rv-surface-2)',
          borderTopColor: 'var(--rv-accent)',
          animation: 'rvSpin 0.8s linear infinite',
        }}
      />
    </div>
  );
}
