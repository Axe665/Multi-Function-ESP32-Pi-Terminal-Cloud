# 静态前端：构建产物直接在镜像里，dist 不进仓库
ARG NODE_IMAGE=node:22-alpine
FROM ${NODE_IMAGE} AS build
WORKDIR /app
ARG NPM_REGISTRY=https://registry.npmjs.org
COPY web/package.json web/package-lock.json* ./
RUN npm install --registry=${NPM_REGISTRY} --no-audit --no-fund
COPY web .
RUN npm run build

FROM caddy:2-alpine
COPY --from=build /app/dist /srv/web
COPY docker/Caddyfile.web /etc/caddy/Caddyfile
