# Deployment

## Environment variables & turbo.json

Turborepo only forwards environment variables to tasks that explicitly declare them. If a server-side module reads `process.env.SOME_KEY` and `SOME_KEY` is not listed in `turbo.json`, the build will either fail (if the code throws on missing values) or silently use `undefined`.

### How it works

In `turbo.json`, each task has an `env` array:

```jsonc
{
  "tasks": {
    "build": {
      "env": [
        "BETTER_AUTH_SECRET",
        "RESEND_API_KEY",
        // ...every env var the build touches
      ]
    }
  }
}
```

Vercel sets these variables in the project settings, but **turbo will not pass them to the build unless they are listed here**. Vercel even prints a warning at the end of failed builds listing the missing variables — check for it.

### When adding a new env var

1. Add it to `.env.local` for local dev
2. Add it to Vercel project settings (Settings > Environment Variables)
3. **Add it to the `env` array in `turbo.json`** — this is the step that gets forgotten

### Defensive coding

Even with `turbo.json` configured correctly, prefer lazy initialization for SDK clients that throw on missing keys:

```ts
// bad — throws at module load during build
const client = new SomeSDK(process.env.API_KEY);

// good — only throws when actually called at runtime
let _client: SomeSDK | null = null;
function getClient() {
  if (!_client) _client = new SomeSDK(process.env.API_KEY);
  return _client;
}
```

Next.js evaluates server modules during `Collecting page data` at build time. A top-level constructor that requires an API key will crash the build even if the key is set in Vercel — because turbo didn't forward it.
