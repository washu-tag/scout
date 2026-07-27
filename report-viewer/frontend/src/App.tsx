import { Outlet } from 'react-router-dom';
import { isEmbedded } from './embed';
import { ChatPromptProvider } from './ChatPrompt';

function App() {
  return (
    <ChatPromptProvider>{isEmbedded() ? <EmbeddedShell /> : <FullShell />}</ChatPromptProvider>
  );
}

function EmbeddedShell() {
  return (
    <div
      style={{
        fontFamily: '-apple-system, BlinkMacSystemFont, "Segoe UI", system-ui, sans-serif',
        color: 'var(--rv-fg)',
        background: 'var(--rv-surface)',
        padding: '0.5rem',
        height: '100vh',
        boxSizing: 'border-box',
        display: 'flex',
        flexDirection: 'column',
        overflow: 'hidden',
      }}
    >
      <Outlet />
    </div>
  );
}

function FullShell() {
  return (
    <div
      style={{
        fontFamily: '-apple-system, BlinkMacSystemFont, "Segoe UI", system-ui, sans-serif',
        color: 'var(--rv-fg)',
        height: '100vh',
        background: 'var(--rv-bg)',
        display: 'flex',
        flexDirection: 'column',
        overflow: 'hidden',
      }}
    >
      <header
        style={{
          padding: '0.75rem 1.5rem',
          background: 'var(--rv-surface)',
          borderBottom: '1px solid var(--rv-border)',
          flex: '0 0 auto',
        }}
      >
        <h1 style={{ margin: 0, fontSize: '1.05rem', fontWeight: 600 }}>Scout Reports</h1>
      </header>
      <main
        style={{
          padding: '1.5rem',
          flex: '1 1 auto',
          minHeight: 0,
          display: 'flex',
          flexDirection: 'column',
          overflow: 'hidden',
        }}
      >
        <Outlet />
      </main>
    </div>
  );
}

export default App;
