import { defineConfig, type Plugin } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'
import { fileURLToPath } from 'url'
import { dirname, resolve } from 'path'
import { existsSync, createReadStream } from 'fs'

const __filename = fileURLToPath(import.meta.url)
const __dirname = dirname(__filename)
const TRACES_FILE = resolve(__dirname, '../crawler/traces/traces.jsonl')

function serveTraces(): Plugin {
  return {
    name: 'serve-traces',
    configureServer(server) {
      server.middlewares.use('/api/traces', (_req, res) => {
        if (existsSync(TRACES_FILE)) {
          res.setHeader('Content-Type', 'application/jsonl')
          createReadStream(TRACES_FILE).pipe(res)
        } else {
          res.statusCode = 404
          res.end('No traces file found at ' + TRACES_FILE)
        }
      })
    },
  }
}

export default defineConfig({
  plugins: [react(), tailwindcss(), serveTraces()],
})
