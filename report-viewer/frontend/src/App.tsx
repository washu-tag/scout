import { Outlet } from 'react-router-dom';
import { isEmbedded } from './embed';

// App shell — outer layout shared by every route. Two modes:
//   1. Standalone (report-viewer.<env>/spa/...): full chrome — banner
//      header, page padding, "All searches" back link.
//   2. Embedded (chat iframe; iframe is cross-origin to chat): chrome
//      stripped — no banner, minimal padding, no back link. The chat
//      itself provides the surrounding context; the iframe is just the
//      table.
function App() {
  const embedded = isEmbedded();
  if (embedded) {
    return (
      <div
        style={{
          fontFamily: '-apple-system, BlinkMacSystemFont, "Segoe UI", system-ui, sans-serif',
          color: '#222',
          background: '#fff',
          padding: '0.5rem',
        }}
      >
        <Outlet />
      </div>
    );
  }
  return (
    <div
      style={{
        fontFamily: '-apple-system, BlinkMacSystemFont, "Segoe UI", system-ui, sans-serif',
        color: '#222',
        minHeight: '100vh',
        background: '#fafafa',
      }}
    >
      <header
        style={{
          padding: '0.75rem 1.5rem',
          background: '#fff',
          borderBottom: '1px solid #e2e2e2',
        }}
      >
        <h1 style={{ margin: 0, fontSize: '1.05rem', fontWeight: 600 }}>Scout Reports</h1>
      </header>
      <main style={{ padding: '1.5rem' }}>
        <Outlet />
      </main>
    </div>
  );
}

export default App;
