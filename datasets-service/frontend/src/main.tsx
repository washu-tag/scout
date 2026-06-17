import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { BrowserRouter, Route, Routes } from 'react-router-dom';

import App from './App';
import DatasetsListPage from './pages/DatasetsListPage';
import DatasetDetailPage from './pages/DatasetDetailPage';

// React Router basename matches the FastAPI StaticFiles mount in app.py
// (/spa/). Vite's `base` only handles asset URLs, not the SPA's own
// internal routes — without basename here, /spa/foo would be parsed as
// /foo and fall through to the FastAPI 404 handler.
const router = (
  <BrowserRouter basename="/spa">
    <Routes>
      <Route path="/" element={<App />}>
        <Route index element={<DatasetsListPage />} />
        <Route path="datasets/:datasetId" element={<DatasetDetailPage />} />
      </Route>
    </Routes>
  </BrowserRouter>
);

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      // Dataset metadata is owned by the caller; tabs of the SPA aren't
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

// Iframe auto-resize. When loaded as an iframe whose parent is on the
// same origin (chat-host alias), grow ourselves to fit content so OWUI's
// `message.embeds` stops pinning the embed to the default ~150-250px.
// Same pattern as the legacy Tabulator viewer in routes/datasets.py.
// Cross-origin case throws on window.frameElement access — silently
// catch and let OWUI's own onload handler (if any) take over.
// Embed iframe height policy: fixed 500px. Big enough to show ~10
// rows + header + pagination, small enough to not push the chat
// composer off-screen. The table container inside owns its own
// vertical scroll so a 1000-row cohort doesn't stretch the iframe.
const EMBED_HEIGHT_PX = 500;

function fitParentIframe() {
  try {
    const f = window.frameElement as HTMLIFrameElement | null;
    if (!f) return;
    f.style.height = EMBED_HEIGHT_PX + 'px';
  } catch {
    /* cross-origin — can't resize */
  }
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
