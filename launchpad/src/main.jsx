import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';
import { GitHubAuthProvider } from './auth/GitHubAuthContext.jsx';
import './index.css';
import App from './App.jsx';

createRoot(document.getElementById('root')).render(
  <StrictMode>
    <GitHubAuthProvider>
      <App />
    </GitHubAuthProvider>
  </StrictMode>,
);
