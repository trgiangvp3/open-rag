import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'
import tailwindcss from '@tailwindcss/vite'

export default defineConfig({
  plugins: [vue(), tailwindcss()],
  server: {
    proxy: {
      '/api': 'http://localhost:8000',
      '/ws': {
        target: 'http://localhost:8000',
        ws: true,
      },
    },
  },
  build: {
    outDir: '../OpenRAG.Api/wwwroot',
    emptyOutDir: true,
  },
})
