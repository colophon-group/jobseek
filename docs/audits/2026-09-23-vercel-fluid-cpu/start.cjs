// Run from apps/web. Credentials are loaded, never printed.
const { createRequire } = require('node:module');
const { resolve } = require('node:path');
if (!process.env.AUDIT_ENV_FILE || !process.env.AUDIT_CPU_LOG) {
  throw new Error('Set AUDIT_ENV_FILE and AUDIT_CPU_LOG to absolute paths.');
}
process.loadEnvFile(process.env.AUDIT_ENV_FILE);
process.env.NEXT_TELEMETRY_DISABLED = '1';
require('./cpu-hook.cjs');
const webRequire = createRequire(resolve('package.json'));
process.argv = [process.execPath, 'next', 'start', '--hostname', '127.0.0.1', '--port', '43189'];
webRequire('next/dist/bin/next');
