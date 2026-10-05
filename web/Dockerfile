# Build do bundle estático
FROM --platform=$BUILDPLATFORM node:26-alpine AS build
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci
COPY . .
RUN npm run build

# Servidor estático + proxy /api para a API Go
FROM nginx:alpine
ENV API_UPSTREAM=http://api:8080
COPY nginx.conf.template /etc/nginx/templates/default.conf.template
COPY --chmod=755 nginx-api-upstream.envsh /docker-entrypoint.d/10-api-upstream.envsh
COPY --from=build /app/dist /usr/share/nginx/html
EXPOSE 80
