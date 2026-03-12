# Use Apify's Playwright image which includes Chromium and all dependencies
FROM apify/actor-node-playwright-chrome:20

# Copy package files and install dependencies
COPY package*.json ./
RUN npm --quiet set progress=false \
    && npm install --omit=dev --omit=optional \
    && echo "Installed npm packages:" \
    && (npm list --omit=dev --all || true) \
    && echo "Node.js version:" \
    && node --version \
    && echo "NPM version:" \
    && npm --version

# Copy built source
COPY dist ./dist

CMD npm start --silent
