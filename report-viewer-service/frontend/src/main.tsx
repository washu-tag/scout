import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { BrowserRouter, Route, Routes } from 'react-router-dom';

import App from './App';
import SearchesListPage from './pages/SearchesListPage';
import SearchDetailPage from './pages/SearchDetailPage';

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

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <QueryClientProvider client={queryClient}>{router}</QueryClientProvider>
  </StrictMode>,
);

// Fixed 500px, delivered via postMessage. OWUI's FullHeightIframe.svelte
// listens for {type:'iframe:height'} and sets the iframe height — works
// cross-origin (frameElement does not). Big enough to show ~10 rows +
// header + pagination, small enough not to push the chat composer off
// screen. The table container inside owns its own vertical scroll past
// that.
const EMBED_HEIGHT_PX = 500;

function fitParentIframe() {
  if (window.parent === window) return; // not embedded
  window.parent.postMessage({ type: 'iframe:height', height: EMBED_HEIGHT_PX }, '*');
}
window.addEventListener('load', fitParentIframe);
// Re-fire after async data lands (TanStack Query fetch → table renders).
setTimeout(fitParentIframe, 300);
setTimeout(fitParentIframe, 1200);
// And whenever the body's resize observer notices a change (sort, page
// flip, etc.) so the iframe tracks content size live.
if (typeof ResizeObserver !== 'undefined') {
  new ResizeObserver(fitParentIframe).observe(document.body);
}
