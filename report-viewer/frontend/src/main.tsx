import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { BrowserRouter, Route, Routes } from 'react-router-dom';

import App from './App';
import SearchesListPage from './pages/SearchesListPage';
import SearchDetailPage from './pages/SearchDetailPage';
import { ApiError, getConfig } from './api/client';
import { setChatOrigin } from './chat';
import { postHeight } from './iframeHeight';
import './theme.css';

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
      retry: (failureCount, error) => {
        if (error instanceof ApiError && error.status >= 400 && error.status < 500) {
          return false;
        }
        return failureCount < 3;
      },
    },
  },
});

// Fetch chat origin from the backend before mounting; a failure leaves
// _chatOrigin empty and cross-frame postMessages no-op silently.
getConfig()
  .then((c) => setChatOrigin(c.chatOrigin))
  .catch(() => {})
  .finally(() => {
    createRoot(document.getElementById('root')!).render(
      <StrictMode>
        <QueryClientProvider client={queryClient}>{router}</QueryClientProvider>
      </StrictMode>,
    );
    window.addEventListener('load', postHeight);
    setTimeout(postHeight, 300);
    setTimeout(postHeight, 1200);
    if (typeof ResizeObserver !== 'undefined') {
      new ResizeObserver(postHeight).observe(document.body);
    }
  });
