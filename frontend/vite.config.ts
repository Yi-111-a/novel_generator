import { defineConfig, loadEnv } from 'vite';
import react from '@vitejs/plugin-react';

// 开发时把 /api 代理到真后端；地址用 VITE_API_BASE_URL 覆盖（默认 localhost:8000）。
export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), '');
  return {
    plugins: [react()],
    server: {
      proxy: {
        '/api': { target: env.VITE_API_BASE_URL || 'http://localhost:8000', changeOrigin: true },
      },
    },
  };
});
