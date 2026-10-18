FROM node:20-alpine AS deps
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci --no-audit --no-fund

FROM node:20-alpine AS builder
WORKDIR /app
ARG API_PROXY_TARGET=http://api:8000
ARG NEXT_PUBLIC_STORE_URL=http://localhost
ARG NEXT_PUBLIC_REDEEM_URL=http://localhost/redeem
ARG NEXT_PUBLIC_CONTACT_WECHAT=MOLIPLY-SUPPORT
ENV API_PROXY_TARGET=$API_PROXY_TARGET \
    NEXT_PUBLIC_STORE_URL=$NEXT_PUBLIC_STORE_URL \
    NEXT_PUBLIC_REDEEM_URL=$NEXT_PUBLIC_REDEEM_URL \
    NEXT_PUBLIC_CONTACT_WECHAT=$NEXT_PUBLIC_CONTACT_WECHAT \
    NEXT_TELEMETRY_DISABLED=1
COPY --from=deps /app/node_modules ./node_modules
COPY . .
RUN npm run build

FROM node:20-alpine AS runner
WORKDIR /app
ENV NODE_ENV=production \
    NEXT_TELEMETRY_DISABLED=1 \
    PORT=3000 \
    HOSTNAME=0.0.0.0

RUN addgroup -S -g 10001 nextjs && adduser -S -u 10001 -G nextjs nextjs

COPY --from=builder /app/public ./public
COPY --from=builder /app/.next/standalone ./
COPY --from=builder /app/.next/static ./.next/static

USER nextjs
EXPOSE 3000

CMD ["node", "server.js"]
