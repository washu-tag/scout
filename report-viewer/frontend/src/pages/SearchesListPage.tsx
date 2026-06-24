import { useQuery } from '@tanstack/react-query';
import { Link } from 'react-router-dom';
import { listSearches, type SearchMeta } from '../api/client';

function fmtTime(iso: string): string {
  const d = new Date(iso);
  if (isNaN(d.getTime())) return iso;
  return d.toLocaleString();
}

function rowStyle(): React.CSSProperties {
  return {
    borderBottom: '1px solid #eee',
    padding: '0.55rem 0.75rem',
  };
}

// Build an OWUI chat URL. The SPA is always served from report-viewer.<env>/spa/...
// (iframe embed and standalone admin view share the host now); swap the
// leading "report-viewer." for "chat." to land on the chat origin.
function chatUrl(chatId: string): string {
  const host = window.location.host;
  const chatHost = host.startsWith('report-viewer.')
    ? 'chat.' + host.slice('report-viewer.'.length)
    : host;
  return `${window.location.protocol}//${chatHost}/c/${encodeURIComponent(chatId)}`;
}

// Group searches by their owui_chat_id. Within each group rows are
// already in newest-first order from the backend's ORDER BY created_at
// DESC. Legacy searches (no chat_id) fall into an "ungrouped" bucket
// so the user can still find them.
function groupByChat(
  searches: SearchMeta[],
): Array<{ chatId: string; title: string; items: SearchMeta[] }> {
  const seen = new Map<string, { title: string; items: SearchMeta[] }>();
  for (const d of searches) {
    const key = d.owui_chat_id || '__ungrouped__';
    const existing = seen.get(key);
    if (existing) {
      existing.items.push(d);
      // First non-empty title wins (most recent in DESC order).
      if (!existing.title && d.owui_chat_title) existing.title = d.owui_chat_title;
    } else {
      seen.set(key, {
        title: d.owui_chat_title || '',
        items: [d],
      });
    }
  }
  // Preserve insertion order — backend already sorted by created_at DESC,
  // and we want groups in that same order (group of the most recent
  // search appears first).
  return Array.from(seen.entries()).map(([chatId, { title, items }]) => ({
    chatId,
    title,
    items,
  }));
}

export default function SearchesListPage() {
  const { data, isLoading, error } = useQuery({
    queryKey: ['searches'],
    queryFn: listSearches,
  });

  if (isLoading) {
    return <p style={{ color: '#666' }}>Loading searches…</p>;
  }
  if (error) {
    return <p style={{ color: '#b00' }}>Failed to load searches: {(error as Error).message}</p>;
  }
  const searches = data ?? [];
  if (searches.length === 0) {
    return (
      <p style={{ color: '#666' }}>
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
        <span style={{ color: '#888', fontSize: '0.85rem' }}>
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

function ChatGroup(props: { group: { chatId: string; title: string; items: SearchMeta[] } }) {
  const { chatId, title, items } = props.group;
  const isUngrouped = chatId === '__ungrouped__';
  // Fall back to a short chat-id slug when the title snapshot is
  // empty (older searches, or OWUI metadata didn't surface the title
  // at create time). Truncate the UUID so the header doesn't run.
  const displayTitle = isUngrouped
    ? 'Searches not tied to a chat'
    : title || `Chat ${chatId.slice(0, 8)}…`;
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
        <h3 style={{ margin: 0, fontSize: '0.9rem', fontWeight: 600, color: '#222' }}>
          {displayTitle}
        </h3>
        <span style={{ color: '#888', fontSize: '0.75rem' }}>
          {items.length} {items.length === 1 ? 'search' : 'searches'}
        </span>
        {!isUngrouped && (
          <a href={chatUrl(chatId)} target="_top" style={{ fontSize: '0.75rem', color: '#4477AA' }}>
            open chat ↗
          </a>
        )}
      </div>
      <div
        style={{
          background: '#fff',
          border: '1px solid #e2e2e2',
          borderRadius: 4,
          overflow: 'hidden',
        }}
      >
        <div
          style={{
            display: 'grid',
            gridTemplateColumns: '1fr 0.7fr 0.6fr 1fr 1fr',
            background: '#f5f5f5',
            fontSize: '0.8rem',
            color: '#555',
            fontWeight: 600,
            ...rowStyle(),
          }}
        >
          <span>ID</span>
          <span>Kind</span>
          <span style={{ textAlign: 'right' }}>Rows</span>
          <span>Created</span>
          <span>Expires</span>
        </div>
        {items.map((d) => (
          <Link
            key={d.id}
            to={`/searches/${d.id}`}
            style={{
              display: 'grid',
              gridTemplateColumns: '1fr 0.7fr 0.6fr 1fr 1fr',
              fontSize: '0.88rem',
              color: '#222',
              textDecoration: 'none',
              ...rowStyle(),
            }}
          >
            <span
              style={{
                fontFamily: 'ui-monospace, SFMono-Regular, Menlo, monospace',
              }}
            >
              {d.id}
            </span>
            <span>{d.kind}</span>
            <span style={{ textAlign: 'right' }}>{d.count.toLocaleString()}</span>
            <span style={{ color: '#555' }}>{fmtTime(d.created_at)}</span>
            <span style={{ color: '#555' }}>{fmtTime(d.expires_at)}</span>
          </Link>
        ))}
      </div>
    </div>
  );
}
