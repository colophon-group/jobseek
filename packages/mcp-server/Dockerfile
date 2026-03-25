FROM node:22-slim AS build
WORKDIR /app
COPY package.json ./
RUN npm install --omit=dev
COPY dist/ ./dist/

FROM node:22-slim
WORKDIR /app
COPY --from=build /app ./
EXPOSE 8080
CMD ["node", "dist/http.js"]
