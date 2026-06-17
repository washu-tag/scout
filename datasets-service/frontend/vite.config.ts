import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

// Build outputs land inside the Python package directory so the existing
// pip-install pipeline (ansible source-bundle → init-container pip install
// /work → /deps) carries the static assets without a separate volume or
// configmap. FastAPI mounts the same directory via StaticFiles at runtime.
export default defineConfig({
  // SPA is mounted at /spa/ in production (FastAPI StaticFiles); base sets the
  // public path so generated asset URLs become /spa/assets/* instead of /assets/*.
  base: '/spa/',
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      // Dev-mode proxy so local API calls don't CORS.
      '/api': 'http://localhost:8000',
    },
  },
  build: {
    outDir: '../src/scout_datasets/static',
    emptyOutDir: true,
    sourcemap: false,
  },
});
