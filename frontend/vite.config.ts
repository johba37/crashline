import tailwindcss from '@tailwindcss/vite'
import react from '@vitejs/plugin-react'
import { defineConfig, searchForWorkspaceRoot } from 'vite'

// https://vite.dev/config/
export default defineConfig({
  plugins: [react(), tailwindcss()],
  // The ABIs and deployed addresses live next to the contracts, outside the project root.
  server: {
    fs: { allow: [searchForWorkspaceRoot(process.cwd()), '../abi', '../deployments'] },
    // The setup page's prices (src/pages/SetupPage.tsx): Yahoo's chart API sends no CORS headers,
    // and answers a browser's User-Agent that comes through a proxy with 429.
    proxy: {
      '/yahoo': {
        target: 'https://query1.finance.yahoo.com',
        changeOrigin: true,
        rewrite: (path) => path.replace(/^\/yahoo/, ''),
        headers: { 'User-Agent': 'Mozilla/5.0' },
      },
    },
  },
})
