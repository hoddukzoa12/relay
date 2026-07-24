# Payments service (TypeScript) — run with tsx, no build step.
FROM node:20-slim AS base
ENV PNPM_HOME=/pnpm
ENV PATH="$PNPM_HOME:$PATH"
RUN corepack enable
WORKDIR /app

# Copy workspace manifests first for better layer caching.
COPY package.json pnpm-workspace.yaml tsconfig.base.json ./
COPY packages/shared/package.json packages/shared/
COPY services/payments/package.json services/payments/

RUN pnpm install --filter @arb/payments...

# Copy source.
COPY packages/shared packages/shared
COPY services/payments services/payments

EXPOSE 8081
CMD ["pnpm", "--filter", "@arb/payments", "start"]
