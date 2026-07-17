import { Outlet } from 'react-router-dom';
import { isEmbedded } from './embed';

function App() {
  if (isEmbedded()) {
    return (
      <div
        style={{
          fontFamily: '-apple-system, BlinkMacSystemFont, "Segoe UI", system-ui, sans-serif',
          color: '#222',
          background: '#fff',
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
  return (
    <div
      style={{
        fontFamily: '-apple-system, BlinkMacSystemFont, "Segoe UI", system-ui, sans-serif',
        color: '#222',
        height: '100vh',
        background: '#fafafa',
        display: 'flex',
        flexDirection: 'column',
        overflow: 'hidden',
      }}
    >
      <header
        style={{
          padding: '0.75rem 1.5rem',
          background: '#fff',
          borderBottom: '1px solid #e2e2e2',
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
