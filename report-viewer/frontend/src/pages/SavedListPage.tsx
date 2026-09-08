import { useQueries } from '@tanstack/react-query';
import { Link } from 'react-router-dom';
import {
  friendlyError,
  listPlots,
  listSearches,
  type PlotMeta,
  type SearchMeta,
} from '../api/client';
import { chatOrigin, chatUrl } from '../chat';

// Searches and charts are separate resources server-side, but a user thinks
// of them as one pile of saved work from their chats, so the homepage merges
// them and each row says which kind it is.
type Kind = 'search' | 'chart';

interface SavedItem {
  kind: Kind;
  id: string;
  created_at: string;
  owui_chat_id: string;
}

function fmtTime(iso: string): string {
  const d = new Date(iso);
  if (isNaN(d.getTime())) return iso;
  return d.toLocaleString();
}

const rowStyle: React.CSSProperties = {
  borderBottom: '1px solid var(--rv-border)',
  padding: '0.55rem 0.75rem',
};

const gridColumns = '5.5rem 1fr 1fr';

function toItem(kind: Kind, m: SearchMeta | PlotMeta): SavedItem {
  return {
    kind,
    id: m.id,
    created_at: m.created_at,
    owui_chat_id: m.owui_chat_id,
  };
}

function groupByChat(items: SavedItem[]): Array<{ chatId: string; items: SavedItem[] }> {
  const seen = new Map<string, SavedItem[]>();
  for (const d of items) {
    const key = d.owui_chat_id || '__ungrouped__';
    const existing = seen.get(key);
    if (existing) {
      existing.push(d);
    } else {
      seen.set(key, [d]);
    }
  }
  return Array.from(seen.entries()).map(([chatId, group]) => ({
    chatId,
    items: group,
  }));
}

function plural(n: number, one: string, many: string): string {
  return `${n} ${n === 1 ? one : many}`;
}

// "12 searches · 3 charts", dropping whichever kind isn't there rather than
// printing "0 charts".
function countLabel(items: SavedItem[]): string {
  const searches = items.filter((d) => d.kind === 'search').length;
  const charts = items.length - searches;
  const parts: string[] = [];
  if (searches > 0) parts.push(plural(searches, 'search', 'searches'));
  if (charts > 0) parts.push(plural(charts, 'chart', 'charts'));
  return parts.join(' · ');
}

export default function SavedListPage() {
  const [searchesQuery, plotsQuery] = useQueries({
    queries: [
      { queryKey: ['searches'], queryFn: listSearches },
      { queryKey: ['plots'], queryFn: listPlots },
    ],
  });

  if (searchesQuery.isLoading || plotsQuery.isLoading) {
    return <p style={{ color: 'var(--rv-muted)' }}>Loading your saved work…</p>;
  }
  // One list failing shouldn't blank the other, so report it above whatever
  // did load.
  const failures = [
    searchesQuery.error ? friendlyError(searchesQuery.error, 'your searches') : null,
    plotsQuery.error ? friendlyError(plotsQuery.error, 'your charts') : null,
  ].filter((m): m is string => m !== null);

  const items = [
    ...(searchesQuery.data ?? []).map((s) => toItem('search', s)),
    ...(plotsQuery.data ?? []).map((p) => toItem('chart', p)),
  ].sort((a, b) => b.created_at.localeCompare(a.created_at));

  if (items.length === 0) {
    return (
      <div>
        {failures.map((m) => (
          <p key={m} style={{ color: 'var(--rv-danger)' }}>
            {m}
          </p>
        ))}
        {failures.length === 0 && (
          <p style={{ color: 'var(--rv-muted)' }}>
            Nothing saved yet. Searches and charts you create in Scout Chat will show up here.
          </p>
        )}
      </div>
    );
  }
  const groups = groupByChat(items);

  return (
    <div>
      {failures.map((m) => (
        <p key={m} style={{ color: 'var(--rv-danger)' }}>
          {m}
        </p>
      ))}
      <div
        style={{
          display: 'flex',
          justifyContent: 'space-between',
          alignItems: 'baseline',
          marginBottom: '1rem',
        }}
      >
        <h2 style={{ margin: 0, fontSize: '1rem', fontWeight: 600 }}>Your searches and charts</h2>
        <span style={{ color: 'var(--rv-muted)', fontSize: '0.85rem' }}>
          {countLabel(items)}
          {groups.length > 1 ? ` across ${groups.length} chats` : ''}
        </span>
      </div>
      {groups.map((g) => (
        <ChatGroup key={g.chatId} group={g} />
      ))}
    </div>
  );
}

function ChatGroup(props: { group: { chatId: string; items: SavedItem[] } }) {
  const { chatId, items } = props.group;
  const isUngrouped = chatId === '__ungrouped__';
  const displayTitle = isUngrouped ? 'Not tied to a chat' : `Chat ${chatId.slice(0, 8)}…`;
  return (
    <div style={{ marginBottom: '1.25rem' }}>
      <div
        style={{
          display: 'flex',
          alignItems: 'baseline',
          gap: '0.6rem',
          marginBottom: '0.4rem',
        }}
      >
        <h3 style={{ margin: 0, fontSize: '0.9rem', fontWeight: 600, color: 'var(--rv-fg)' }}>
          {displayTitle}
        </h3>
        <span style={{ color: 'var(--rv-muted)', fontSize: '0.75rem' }}>{countLabel(items)}</span>
        {!isUngrouped && chatOrigin() && (
          <a
            href={chatUrl(chatId)}
            target="_top"
            style={{ fontSize: '0.75rem', color: 'var(--rv-accent)' }}
          >
            open chat ↗
          </a>
        )}
      </div>
      <div
        style={{
          background: 'var(--rv-surface)',
          border: '1px solid var(--rv-border)',
          borderRadius: 4,
          overflow: 'hidden',
        }}
      >
        <div
          style={{
            display: 'grid',
            gridTemplateColumns: gridColumns,
            background: 'var(--rv-surface-2)',
            fontSize: '0.8rem',
            color: 'var(--rv-muted)',
            fontWeight: 600,
            ...rowStyle,
          }}
        >
          <span>Type</span>
          <span>ID</span>
          <span>Created</span>
        </div>
        {items.map((d) => (
          <Link
            key={`${d.kind}-${d.id}`}
            to={d.kind === 'chart' ? `/plots/${d.id}` : `/searches/${d.id}`}
            style={{
              display: 'grid',
              gridTemplateColumns: gridColumns,
              fontSize: '0.88rem',
              color: 'var(--rv-fg)',
              textDecoration: 'none',
              alignItems: 'center',
              ...rowStyle,
            }}
          >
            <KindBadge kind={d.kind} />
            <span
              style={{
                fontFamily: 'ui-monospace, SFMono-Regular, Menlo, monospace',
              }}
            >
              {d.id}
            </span>
            <span style={{ color: 'var(--rv-muted)' }}>{fmtTime(d.created_at)}</span>
          </Link>
        ))}
      </div>
    </div>
  );
}

function KindBadge(props: { kind: Kind }) {
  const isChart = props.kind === 'chart';
  return (
    <span
      style={{
        justifySelf: 'start',
        padding: '0.05rem 0.4rem',
        borderRadius: 999,
        border: '1px solid var(--rv-border)',
        background: 'var(--rv-surface-2)',
        color: isChart ? 'var(--rv-accent)' : 'var(--rv-muted)',
        fontSize: '0.7rem',
        fontWeight: 600,
        letterSpacing: '0.02em',
      }}
    >
      {isChart ? 'Chart' : 'Search'}
    </span>
  );
}
