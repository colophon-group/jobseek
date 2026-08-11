import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'

const indexPath = fileURLToPath(new URL('../dist/index.html', import.meta.url))
const html = readFileSync(indexPath, 'utf8')

const cspTag = html.match(
  /<meta\b[^>]*http-equiv=["']Content-Security-Policy["'][^>]*>/i,
)
assert.ok(cspTag, 'built index.html must contain a Content Security Policy')

const encodedCsp = cspTag[0].match(/\bcontent="([^"]+)"/i)?.[1]
assert.ok(encodedCsp, 'built CSP meta tag must have content')
const csp = encodedCsp
  .replace(/&#(?:39|x27);/gi, "'")
  .replace(/&apos;/gi, "'")
  .replace(/&amp;/gi, '&')
assert.match(csp, /(?:^|;)\s*script-src 'self'(?:;|$)/)
assert.match(csp, /(?:^|;)\s*object-src 'none'(?:;|$)/)
assert.match(csp, /(?:^|;)\s*base-uri 'none'(?:;|$)/)
const scriptSrc = csp.match(/(?:^|;)\s*(script-src[^;]*)/)?.[1]
assert.ok(scriptSrc, 'built CSP must define script-src')
assert.doesNotMatch(scriptSrc, /'unsafe-inline'|'unsafe-eval'/)

for (const script of html.matchAll(/<script\b([^>]*)>([\s\S]*?)<\/script>/gi)) {
  assert.match(script[1], /\bsrc=/i, 'built scripts must load from external files')
  assert.equal(script[2].trim(), '', 'built index.html must not depend on inline script')
}

console.log('trace-viewer build security: CSP and external scripts verified')
