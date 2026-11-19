import tailwindcss from '@tailwindcss/vite';
import react from '@vitejs/plugin-react';
import path from 'path';
import {defineConfig} from 'vite';

export default defineConfig(() => {
  return {
    plugins: [react(), tailwindcss()],
    resolve: {
      alias: {
        '@': path.resolve(__dirname, '.'),
      },
    },
    server: {
      // HMR is disabled in AI Studio container preview environment.
      hmr: false,
      // Disable file watching when DISABLE_HMR is true to save CPU during agent edits.
      watch: process.env.DISABLE_HMR === 'true' ? null : {},
      proxy: {
        '/api': {
          target: process.env.BACKEND_URL || 'https://leads-einq.onrender.com',
          changeOrigin: true,
          secure: false,
        },
        '/health': {
          target: process.env.BACKEND_URL || 'https://leads-einq.onrender.com',
          changeOrigin: true,
          secure: false,
        },
      },
    },
  };
});
