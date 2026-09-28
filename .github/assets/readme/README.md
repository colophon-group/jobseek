# README screenshots

These are product screenshots, not generated mockups. Keep labels and controls consistent with the product; recapture images when a workflow changes.

- `explore.png`: existing English product capture (`feature1-light.png`) with an applied Software Engineer filter and corresponding results.
- `company.png`: existing README company-page capture.
- `watchlists.png`: authenticated product capture, 28 September 2026. Software engineering in Switzerland watchlist with ABB, Google, and Siemens. The share action uses the current share icon.
- `narrowed.png`: shared-view production capture, 28 September 2026. The Swiss robotics employers watchlist with its saved matching request and real Narrowed results.
- `application-tracker.png`: existing English product screenshot (`apps/web/public/screenshots/en/feature2-light.png`), showing application stages and job details.

Recapture the current watchlist marketing images and Narrowed README image from `apps/web/` with `pnpm screenshots --base-url https://jseek.co --features feature3,narrowed --storage-state /private/path/session.json`. The session file is private and must never be committed. The capture checks authentication, the requested locale, loaded company links, and completed Narrowed results before writing an image. Narrowed is photographed in its shared guest view so opening it cannot start an owner evaluation.
