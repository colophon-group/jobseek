# Web origin on Hetzner and Cloudflare Free

The full Next.js app runs as a standalone Node process on the existing crawler host. It listens only on `127.0.0.1:3000`; a dedicated Cloudflare Tunnel publishes `jseek.co`. The candidate listens on `127.0.0.1:3001`. Release directories are immutable, with the Next cache under `/var/cache/jobseek-web`. The existing crawler containers and deployment contract are independent of this service.

## Protected configuration

GitHub environment `Production` holds `WEB_ORIGIN_RUNTIME_JSON`, `WEB_ORIGIN_TUNNEL_TOKEN`, and the existing `NEXT_SERVER_ACTIONS_ENCRYPTION_KEY`, host SSH credentials, and migration database URL. `WEB_ORIGIN_RUNTIME_JSON` has the shape `{ "env": { "KEY": "value" } }`. It is a protected secret, never a repository file or workflow output. `deploy/web-origin/validate-config.mjs` checks required app settings and rejects provider administration credentials. The deployment workflow adds the stable Server Actions key from its existing GitHub secret before building and staging.

Use the production settings in Doppler `jobseek-web-runtime/prd_vercel_production` as the starting point. Map the existing `STRIPE_LIVE_SECRET_API_KEY` to `STRIPE_SECRET_KEY` and `PADDLE_API_TOKEN` to `PADDLE_API_KEY` from the protected crawler environment. The Narrowed execution flags and receipt are in the protected GitHub `NARROWED_RUNTIME_POLICY_JSON` variable; its HMAC and Typesafe AI token are in Doppler `jobseek-web-runtime/prd_github_web`. The Murmur token is in Doppler `jobseek-ci-runtime/prd_murmur`. Set `TRUSTED_ORIGINS=https://jseek.co`, `JOBSEEK_WEB_ORIGIN=cloudflare_tunnel`, and `VERCEL_ENV=production`; omit `VERCEL` and provider administration tokens. Keep `INDEXNOW_KEY`, `CRON_SECRET`, and all active payment, auth, search, Redis, email, R2, and database settings.

The existing Paddle and Resend webhook signing secrets can be read through their provider APIs. A new Stripe endpoint with the same events and API version has been created and **disabled**; its new secret is in the protected runtime draft. Enable the new endpoint and disable the old endpoint after the site is serving through Cloudflare. The old unsubscribe signing secret is not retrievable from Vercel. A new signing secret is in the runtime draft; links in emails signed with the old secret will stop verifying, while recipients can still sign in and pause alerts from Settings. The operator requested `JOB_ALERTS_MODE=off`, so the Hetzner timer remains disabled.

The web database is `web-postgres.jseek.co:5433`; the crawler host can reach this existing TLS endpoint. The workflow verifies migration head before building.

## Stage and activate

1. Merge the reviewed code after required checks pass. Use the exact current `main` SHA in every dispatch.
2. Dispatch `deploy-web-origin.yml` with `stage`. It builds the standalone artifact, transfers it over pinned SSH, starts the candidate, and checks `/en/sign-in` on loopback. No public routing changes.
3. Dispatch the same workflow with `activate`. It checks the candidate, switches the production config, and verifies port 3000. The alert timer follows the protected mode setting and stays disabled while `off`. The prior production config is restored if activation fails.
4. Dispatch `tunnel`. It installs a dedicated pinned `cloudflared` binary and token and starts `jobseek-web-tunnel.service`. The shared Typesense tunnel binary and service are untouched. Confirm the tunnel reports healthy in Cloudflare and the origin answers on loopback.
5. Change only the apex `jseek.co` address record from its current Vercel A record to a **proxied CNAME** targeting `2cc47ac8-ebce-4c99-9139-cf75b681d47c.cfargotunnel.com`. Re-read the apex address records immediately before this change; preserve MX, TXT, SPF, DKIM, and every other record. Cloudflare's DNS batch API can delete the old address record and create the CNAME in one database transaction. Distributed DNS propagation is not atomic, so verify from multiple resolvers and test the public site. Keep the old record details for rollback.
6. Verify Cloudflare's **Add visitor location headers** Managed Transform remains enabled for the zone. It was enabled through the dashboard before cutover. The app reads `cf-iplatitude` and `cf-iplongitude` for location-aware search.
7. Enable the new Stripe endpoint and disable the old endpoint. Check public home, Explore, sign-in, an auth callback, company page, payment checkout/webhooks, sitemap, and `indexnow-key.txt`. Then dispatch `notify-blog-indexnow.yml` for the deployed SHA.

Rollback: restore the saved apex A record through the same DNS batch API if public checks fail. Restore the previous `/etc/jobseek-web/production.json` only if the origin itself is unhealthy; the activation script already does this for immediate failures. Do not change crawler writer services or their deployment state.

The old Vercel workflow remains available only by manual dispatch during the transition. It no longer runs on pushes to `main`.
