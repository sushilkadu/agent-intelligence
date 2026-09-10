# Build context is apps/frontend itself (docker-compose.yml sets
# `context: ./apps/frontend`) -- unlike the Python services, the
# frontend has no cross-package relative-path installs that need a
# repo-root context.
#
# node:20-slim, not the default `node:lts`/`node:latest` -- Next.js
# 16.x (apps/frontend/package.json) requires Node >=20.9 (see
# node_modules/next's own package.json `engines` field).
FROM node:20-slim

WORKDIR /app

COPY package.json package-lock.json ./
RUN npm ci

COPY . .

EXPOSE 3000

# `next dev`, not a production build -- this image is for interactive
# local manual testing (see docker-compose.yml), hot-reload is a nice
# bonus, not a requirement. `-H 0.0.0.0` so the dev server is reachable
# from outside the container via the published port, not just from
# localhost inside it.
CMD ["npx", "next", "dev", "-H", "0.0.0.0", "-p", "3000"]
