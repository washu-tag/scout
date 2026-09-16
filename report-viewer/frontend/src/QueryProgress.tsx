import { useEffect, useRef, useState } from 'react';
import type { QueryProgress } from './api/client';

const POLL_MS = 3000;
const TICK_MS = 250;
// A faster query finishes before the indicator is worth showing.
const SHOW_AFTER_MS = 1000;

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
    // One last read: a running query's poll is short of the final totals.
    let cancelled = false;
    fetchProgress()
      .then((p) => {
        if (!cancelled && p.done) setProgress(p);
      })
      .catch(() => {});
    setDone(true);
    return () => {
      cancelled = true;
    };
  }, [loading, fetchProgress]);

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
  PLANNING: 'Planning query',
  STARTING: 'Starting up',
  RUNNING: 'Searching',
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
  // Trino saturates at 100 against a denominator that is still filling in, then
  // freezes there once the scan ends.
  if (p?.state === 'RUNNING' && p.progressPercentage != null) {
    parts.push(`${Math.min(99, Math.round(p.progressPercentage))}%`);
  }
  // Input counters, so a plan that reads a table twice reports twice its rows.
  const rows = p?.processedRows ? `${compactNumber(p.processedRows)} rows` : null;
  const bytes = p?.processedBytes ? compactBytes(p.processedBytes) : null;
  if (rows || bytes) parts.push(`${[rows, bytes].filter(Boolean).join(', ')} read`);
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
      }}
    >
      {`${done ? doneLabel : stateLine(progress)} · ${detailLine(progress, seconds)}`}
    </span>
  );
}

// `fill` centres in a positioned ancestor's visible box rather than in flow,
// so a table wider or taller than its scroll container doesn't shift it.
export function LoadingSpinner({
  show,
  minHeight,
  fill,
}: {
  show: boolean;
  minHeight?: number;
  fill?: boolean;
}) {
  if (!show) return null;
  return (
    <div
      style={{
        minHeight,
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
        ...(fill ? { position: 'absolute', inset: 0 } : null),
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
