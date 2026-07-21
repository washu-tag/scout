import { useQuery } from '@tanstack/react-query';
import { Link } from 'react-router-dom';
import { friendlyError, listSearches, type SearchMeta } from '../api/client';
import { chatOrigin, chatUrl } from '../chat';

function fmtTime(iso: string): string {
  const d = new Date(iso);
  if (isNaN(d.getTime())) return iso;
  return d.toLocaleString();
}

const rowStyle: React.CSSProperties = {
  borderBottom: '1px solid var(--rv-border)',
  padding: '0.55rem 0.75rem',
};

function groupByChat(searches: SearchMeta[]): Array<{ chatId: string; items: SearchMeta[] }> {
  const seen = new Map<string, SearchMeta[]>();
  for (const d of searches) {
    const key = d.owui_chat_id || '__ungrouped__';
    const existing = seen.get(key);
    if (existing) {
      existing.push(d);
    } else {
      seen.set(key, [d]);
    }
  }
  return Array.from(seen.entries()).map(([chatId, items]) => ({
    chatId,
    items,
  }));
}

export default function SearchesListPage() {
  const { data, isLoading, error } = useQuery({
    queryKey: ['searches'],
    queryFn: listSearches,
  });

  if (isLoading) {
    return <p style={{ color: 'var(--rv-muted)' }}>Loading searches…</p>;
  }
  if (error) {
    return <p style={{ color: 'var(--rv-danger)' }}>{friendlyError(error, 'your searches')}</p>;
  }
  const searches = data ?? [];
  if (searches.length === 0) {
    return (
      <p style={{ color: 'var(--rv-muted)' }}>
        No searches yet. Searches you run in Scout Chat will show up here.
      </p>
    );
  }
  const groups = groupByChat(searches);

  return (
    <div>
      <div
        style={{
          display: 'flex',
          justifyContent: 'space-between',
          alignItems: 'baseline',
          marginBottom: '1rem',
        }}
      >
        <h2 style={{ margin: 0, fontSize: '1rem', fontWeight: 600 }}>Your searches</h2>
        <span style={{ color: 'var(--rv-muted)', fontSize: '0.85rem' }}>
          {searches.length} {searches.length === 1 ? 'search' : 'searches'}
          {groups.length > 1 ? ` across ${groups.length} chats` : ''}
        </span>
      </div>
      {groups.map((g) => (
        <ChatGroup key={g.chatId} group={g} />
      ))}
    </div>
  );
}

function ChatGroup(props: { group: { chatId: string; items: SearchMeta[] } }) {
  const { chatId, items } = props.group;
  const isUngrouped = chatId === '__ungrouped__';
  const displayTitle = isUngrouped ? 'Searches not tied to a chat' : `Chat ${chatId.slice(0, 8)}…`;
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
        <span style={{ color: 'var(--rv-muted)', fontSize: '0.75rem' }}>
          {items.length} {items.length === 1 ? 'search' : 'searches'}
        </span>
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
            gridTemplateColumns: '1fr 0.6fr 1fr',
            background: 'var(--rv-surface-2)',
            fontSize: '0.8rem',
            color: 'var(--rv-muted)',
            fontWeight: 600,
            ...rowStyle,
          }}
        >
          <span>ID</span>
          <span>Rows</span>
          <span>Created</span>
        </div>
        {items.map((d) => (
          <Link
            key={d.id}
            to={`/searches/${d.id}`}
            style={{
              display: 'grid',
              gridTemplateColumns: '1fr 0.6fr 1fr',
              fontSize: '0.88rem',
              color: 'var(--rv-fg)',
              textDecoration: 'none',
              ...rowStyle,
            }}
          >
            <span
              style={{
                fontFamily: 'ui-monospace, SFMono-Regular, Menlo, monospace',
              }}
            >
              {d.id}
            </span>
            <span>{d.count === null ? '—' : d.count.toLocaleString()}</span>
            <span style={{ color: 'var(--rv-muted)' }}>{fmtTime(d.created_at)}</span>
          </Link>
        ))}
      </div>
    </div>
  );
}
