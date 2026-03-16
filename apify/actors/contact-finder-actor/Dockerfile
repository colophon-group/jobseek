FROM apify/actor-node:20

# Copy package files and install dependencies
COPY package*.json ./
RUN npm --quiet set progress=false \
    && npm install --include=dev

# Copy the rest of the files and build
COPY . .
RUN npm run build \
    && echo "Node.js version:" && node --version \
    && echo "NPM version:" && npm --version

CMD npm start --silent
