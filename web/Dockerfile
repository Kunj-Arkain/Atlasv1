# ═══════════════════════════════════════════════════════════════
# Arkain Web — Next.js Production Dockerfile
# ═══════════════════════════════════════════════════════════════

# ── Stage 1: Install deps ────────────────────────────────────
FROM node:20-alpine AS deps
WORKDIR /app
COPY package.json ./
RUN npm install --production=false

# ── Stage 2: Build ───────────────────────────────────────────
FROM node:20-alpine AS builder
WORKDIR /app
COPY --from=deps /app/node_modules ./node_modules
COPY . .

# NEXT_PUBLIC_ vars must be present at build time
ARG NEXT_PUBLIC_API_MODE=fastapi
ARG NEXT_PUBLIC_API_BASE_URL
ARG NEXT_PUBLIC_ENABLE_AUTH=true

ENV NEXT_PUBLIC_API_MODE=$NEXT_PUBLIC_API_MODE
ENV NEXT_PUBLIC_API_BASE_URL=$NEXT_PUBLIC_API_BASE_URL
ENV NEXT_PUBLIC_ENABLE_AUTH=$NEXT_PUBLIC_ENABLE_AUTH
ENV NEXT_TELEMETRY_DISABLED=1

RUN npm run build

# ── Stage 3: Production ─────────────────────────────────────
FROM node:20-alpine AS runner
WORKDIR /app
ENV NODE_ENV=production
ENV NEXT_TELEMETRY_DISABLED=1

RUN addgroup --system --gid 1001 nodejs
RUN adduser --system --uid 1001 nextjs

COPY --from=builder /app/public ./public
COPY --from=builder --chown=nextjs:nodejs /app/.next/standalone ./
COPY --from=builder --chown=nextjs:nodejs /app/.next/static ./.next/static

USER nextjs
EXPOSE 3000
ENV PORT=3000
CMD ["node", "server.js"]
