# Build stage
FROM node:20-slim AS build

WORKDIR /app

# Copy package files
COPY frontend/package*.json ./
RUN npm install

# Copy frontend code
COPY frontend/ ./
COPY .env* ./

# Build the application
RUN npm run build

# Production stage
FROM nginx:stable-alpine

# Copy build artifacts
COPY --from=build /app/dist /usr/share/nginx/html

# Copy Nginx config
COPY nginx.conf /etc/nginx/conf.d/default.conf

# Expose port
EXPOSE 80

CMD ["nginx", "-g", "daemon off;"]
