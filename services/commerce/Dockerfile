# Commerce service (TypeScript) — run with tsx, no build step.
FROM node:20-slim AS base
ENV PNPM_HOME=/pnpm
ENV PATH="$PNPM_HOME:$PATH"
RUN corepack enable
WORKDIR /app

COPY package.json pnpm-workspace.yaml tsconfig.base.json ./
COPY packages/shared/package.json packages/shared/
COPY services/commerce/package.json services/commerce/

RUN pnpm install --filter @arb/commerce...

COPY packages/shared packages/shared
COPY services/commerce services/commerce

EXPOSE 8082
CMD ["pnpm", "--filter", "@arb/commerce", "start"]
