# Stage 1: build the React app (Node only exists in this stage).
FROM node:20-alpine AS build
WORKDIR /app
COPY dashboard/package.json dashboard/package-lock.json ./
RUN npm ci
COPY dashboard/ ./
RUN npm run build

# Stage 2: serve the static files with nginx. The browser connects to MQTT itself
# (ws://<page host>:9001), so nginx only serves files.
FROM nginx:1.27-alpine
COPY --from=build /app/dist /usr/share/nginx/html
