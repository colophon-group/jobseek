import { defineConfig, type Plugin } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'

const PRODUCTION_CSP = [
  "default-src 'none'",
  "script-src 'self'",
  "style-src 'self' 'unsafe-inline'",
  "connect-src 'self' https://huggingface.co https://*.huggingface.co https://*.hf.co",
  "img-src 'self' data:",
  "font-src 'self'",
  "base-uri 'none'",
  "form-action 'none'",
  "object-src 'none'",
  "frame-src 'none'",
  "worker-src 'none'",
].join('; ')

const productionCspPlugin: Plugin = {
  name: 'trace-viewer-production-csp',
  apply: 'build',
  transformIndexHtml: {
    order: 'post',
    handler() {
      return [{
        tag: 'meta',
        attrs: {
          'http-equiv': 'Content-Security-Policy',
          content: PRODUCTION_CSP,
        },
        injectTo: 'head-prepend',
      }]
    },
  },
}

export default defineConfig({
  plugins: [react(), tailwindcss(), productionCspPlugin],
})
