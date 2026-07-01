import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { BrowserRouter, Route, Routes } from 'react-router-dom';

import App from './App';
import SearchesListPage from './pages/SearchesListPage';
import SearchDetailPage from './pages/SearchDetailPage';
import { postHeight } from './iframeHeight';

// basename must match the FastAPI StaticFiles mount (/spa/); Vite's `base`
// only handles asset URLs, not React Router's internal routes.
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
      staleTime: 30_000,
      refetchOnWindowFocus: false,
    },
  },
});

const pulseStyle = document.createElement('style');
pulseStyle.textContent =
  '@keyframes scoutSpin{to{transform:rotate(360deg)}}' +
  '.scout-col-resize{border-right:1px solid #d0d0d0}' +
  '.scout-col-resize:hover{border-right-color:#888}' +
  'tr.scout-row:hover{background:#f5f7f9 !important}';
document.head.appendChild(pulseStyle);

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <QueryClientProvider client={queryClient}>{router}</QueryClientProvider>
  </StrictMode>,
);

// Belt-and-suspenders around ResizeObserver: catch late layout shifts
// from font loads / third-party CSS that the observer can miss.
window.addEventListener('load', postHeight);
setTimeout(postHeight, 300);
setTimeout(postHeight, 1200);
if (typeof ResizeObserver !== 'undefined') {
  new ResizeObserver(postHeight).observe(document.body);
}
