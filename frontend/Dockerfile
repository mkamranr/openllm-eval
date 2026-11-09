# syntax=docker/dockerfile:1
# Frontend image: static build served by unprivileged nginx, which also proxies /api to the API.

ARG NODE_IMAGE=node:20.18.1-alpine3.20
ARG NGINX_IMAGE=nginxinc/nginx-unprivileged:1.27.3-alpine

FROM ${NODE_IMAGE} AS build
WORKDIR /src
COPY package.json package-lock.json ./
RUN --mount=type=cache,target=/root/.npm npm ci --no-audit --no-fund
COPY . .
RUN npm run build

FROM ${NGINX_IMAGE} AS runtime
COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /src/dist /usr/share/nginx/html
EXPOSE 8080
HEALTHCHECK --interval=10s --timeout=3s --retries=5 \
    CMD wget -q -O /dev/null http://127.0.0.1:8080/ || exit 1
