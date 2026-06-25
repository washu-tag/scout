import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { BrowserRouter, Route, Routes } from 'react-router-dom';

import App from './App';
import SearchesListPage from './pages/SearchesListPage';
import SearchDetailPage from './pages/SearchDetailPage';
import { postHeight } from './iframeHeight';

// React Router basename matches the FastAPI StaticFiles mount in app.py
// (/spa/). Vite's `base` only handles asset URLs, not the SPA's own
// internal routes — without basename here, /spa/foo would be parsed as
// /foo and fall through to the FastAPI 404 handler.
const router = (
  <BrowserRouter basename="/spa">
    <Routes>
      <Route path="/" element={<App />}>
        <Route index element={<SearchesListPage />} />
        <Route path="searches/:searchId" element={<SearchDetailPage />} />
      </Route>
    </Routes>
  </BrowserRouter>
);

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      // Search metadata is owned by the caller; tabs of the SPA aren't
      // racing each other. 30s keeps "refresh on focus" from being noisy.
      staleTime: 30_000,
      refetchOnWindowFocus: false,
    },
  },
});

const pulseStyle = document.createElement('style');
pulseStyle.textContent =
  '@keyframes scoutPulse{0%,100%{opacity:1}50%{opacity:.25}}' +
  '@keyframes scoutSpin{to{transform:rotate(360deg)}}';
document.head.appendChild(pulseStyle);

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <QueryClientProvider client={queryClient}>{router}</QueryClientProvider>
  </StrictMode>,
);

// Fire size updates at load + after a couple async ticks so the iframe
// tracks content size live. Height value lives in iframeHeight.ts so
// the in-page toggle can flip compact/expanded without competing here.
window.addEventListener('load', postHeight);
setTimeout(postHeight, 300);
setTimeout(postHeight, 1200);
if (typeof ResizeObserver !== 'undefined') {
  new ResizeObserver(postHeight).observe(document.body);
}
