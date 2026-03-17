FROM node:22-bookworm-slim AS builder

WORKDIR /app
ENV NODE_ENV=production

RUN corepack enable

COPY package.json pnpm-lock.yaml pnpm-workspace.yaml ./
COPY apps/signals apps/signals

RUN pnpm install --frozen-lockfile --filter @jobseek/signals...

WORKDIR /app/apps/signals
RUN pnpm build

FROM node:22-bookworm-slim AS runner

WORKDIR /app
ENV NODE_ENV=production
ENV PORT=3000
ENV HOSTNAME=0.0.0.0

COPY --from=builder /app/apps/signals/.next/standalone ./
COPY --from=builder /app/apps/signals/.next/static ./apps/signals/.next/static

EXPOSE 3000

CMD ["node", "apps/signals/server.js"]
