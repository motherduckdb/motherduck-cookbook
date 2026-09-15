import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';
import path from 'path';

export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: {
      // Same import path as the Dive runtime; served here by the fixture shim.
      '@motherduck/react-sql-query': path.resolve(__dirname, 'src/md-sdk.tsx'),
    },
  },
});
