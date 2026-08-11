import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import { JSDOM } from 'jsdom'

const indexPath = fileURLToPath(new URL('../dist/index.html', import.meta.url))
const html = readFileSync(indexPath, 'utf8')
const document = new JSDOM(html).window.document

const cspTag = [...document.querySelectorAll('meta[http-equiv]')].find(
  (meta) => meta.getAttribute('http-equiv')?.toLowerCase() === 'content-security-policy',
)
assert.ok(cspTag, 'built index.html must contain a Content Security Policy')

const csp = cspTag.getAttribute('content')
assert.ok(csp, 'built CSP meta tag must have content')
assert.match(csp, /(?:^|;)\s*script-src 'self'(?:;|$)/)
assert.match(csp, /(?:^|;)\s*object-src 'none'(?:;|$)/)
assert.match(csp, /(?:^|;)\s*base-uri 'none'(?:;|$)/)
const scriptSrc = csp.match(/(?:^|;)\s*(script-src[^;]*)/)?.[1]
assert.ok(scriptSrc, 'built CSP must define script-src')
assert.doesNotMatch(scriptSrc, /'unsafe-inline'|'unsafe-eval'/)

for (const script of document.querySelectorAll('script')) {
  assert.ok(script.hasAttribute('src'), 'built scripts must load from external files')
  assert.equal(script.textContent?.trim(), '', 'built index.html must not depend on inline script')
}

console.log('trace-viewer build security: CSP and external scripts verified')
