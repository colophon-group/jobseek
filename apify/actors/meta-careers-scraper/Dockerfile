# Use Apify's Playwright image which includes Chromium and all dependencies
FROM apify/actor-node-playwright-chrome:20

# Copy package files and install dependencies
COPY package*.json ./
COPY src ./src
COPY tsconfig.json ./
RUN npm --quiet set progress=false \
    && npm install --include=dev \
    && ./node_modules/.bin/tsc --build \
    && echo "Node.js version:" && node --version \
    && echo "NPM version:" && npm --version

CMD npm start --silent
