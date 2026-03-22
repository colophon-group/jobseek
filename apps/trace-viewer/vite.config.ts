import { defineConfig, type Plugin } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'

function serveTraces(): Plugin {
  return {
    name: 'serve-traces',
    configureServer(server) {
      server.middlewares.use('/api/traces', async (_req, res) => {
        const fs = await import('fs')
        const path = await import('path')
        const file = path.resolve(__dirname, '../../crawler/traces/traces.jsonl')
        if (fs.existsSync(file)) {
          res.setHeader('Content-Type', 'application/jsonl')
          fs.createReadStream(file).pipe(res)
        } else {
          res.statusCode = 404
          res.end('No traces file found')
        }
      })
    },
  }
}

export default defineConfig({
  plugins: [react(), tailwindcss(), serveTraces()],
})
