import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';
import { AuthProvider } from 'react-oidc-context';
import './index.css';
import App from './App.jsx';
import { authConfig } from './auth/authConfig.js';

createRoot(document.getElementById('root')).render(
  <StrictMode>
    <AuthProvider {...authConfig}>
      <App />
    </AuthProvider>
  </StrictMode>,
);
