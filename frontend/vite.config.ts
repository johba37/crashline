import tailwindcss from '@tailwindcss/vite'
import react from '@vitejs/plugin-react'
import { defineConfig, searchForWorkspaceRoot } from 'vite'

// https://vite.dev/config/
export default defineConfig({
  plugins: [react(), tailwindcss()],
  // The ABIs and deployed addresses live next to the contracts, outside the project root.
  server: { fs: { allow: [searchForWorkspaceRoot(process.cwd()), '../abi', '../deployments'] } },
})
