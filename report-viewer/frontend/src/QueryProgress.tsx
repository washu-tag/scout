import { useEffect, useState } from 'react';
import type { QueryProgress } from './api/client';

const POLL_MS = 1000;

/** Polls `fetchProgress` while `active`. Errors are swallowed: progress is
 * best-effort and a miss just leaves the bar indeterminate. */
export function useQueryProgress(
  active: boolean,
  fetchProgress: () => Promise<QueryProgress>,
): QueryProgress | null {
  const [progress, setProgress] = useState<QueryProgress | null>(null);

  useEffect(() => {
    if (!active) {
      setProgress(null);
      return;
    }
    let cancelled = false;
    const tick = async () => {
      try {
        const p = await fetchProgress();
        if (!cancelled) setProgress(Object.keys(p).length ? p : null);
      } catch {
        /* keep the last value */
      }
    };
    tick();
    const id = setInterval(tick, POLL_MS);
    return () => {
      cancelled = true;
      clearInterval(id);
    };
  }, [active, fetchProgress]);

  return progress;
}

const STATE_LABELS: Record<string, string> = {
  QUEUED: 'Waiting for cluster capacity',
  PLANNING: 'Planning the query',
  STARTING: 'Starting',
  RUNNING: 'Scanning reports',
  FINISHING: 'Fetching results',
};

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

function detail(p: QueryProgress): string {
  const parts: string[] = [];
  if (p.processedRows) parts.push(`${compactNumber(p.processedRows)} rows`);
  if (p.processedBytes) parts.push(compactBytes(p.processedBytes));
  if (p.elapsedTimeMillis) parts.push(`${Math.round(p.elapsedTimeMillis / 1000)}s`);
  return parts.join(' · ');
}

/** Centred loading block: label, a thin bar, and one line of Trino stats.
 * The percentage is shown exactly as Trino reports it, including going
 * backwards as it discovers more splits. */
export function QueryProgressBar({
  label,
  progress,
}: {
  label: string;
  progress: QueryProgress | null;
}) {
  const pct = progress?.progressPercentage;
  const line = progress ? detail(progress) : '';
  const state = progress?.state ? STATE_LABELS[progress.state] : null;

  return (
    <div
      style={{
        display: 'flex',
        flexDirection: 'column',
        alignItems: 'center',
        justifyContent: 'center',
        gap: '0.5rem',
        padding: '2rem 1rem',
        color: 'var(--rv-muted)',
        fontSize: '0.8rem',
      }}
    >
      <div>{state ?? label}</div>
      <div
        style={{
          width: 'min(260px, 60%)',
          height: 4,
          borderRadius: 2,
          background: 'var(--rv-surface-2)',
          overflow: 'hidden',
        }}
      >
        <div
          style={{
            height: '100%',
            borderRadius: 2,
            background: 'var(--rv-accent)',
            width: pct == null ? '30%' : `${Math.min(100, Math.max(0, pct))}%`,
            transition: 'width 0.4s ease',
            animation: pct == null ? 'rvIndeterminate 1.4s ease-in-out infinite' : undefined,
          }}
        />
      </div>
      {(pct != null || line) && (
        <div style={{ fontSize: '0.7rem', fontVariantNumeric: 'tabular-nums' }}>
          {pct != null && <span>{pct.toFixed(0)}%</span>}
          {pct != null && line && <span> · </span>}
          {line}
        </div>
      )}
    </div>
  );
}
