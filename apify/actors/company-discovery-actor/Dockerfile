# Use playwright-chrome for full glibc stack (node-tls-client Go binary needs libresolv.so.2)
# We don't launch any browser — this image is just chosen for its complete system libraries.
FROM apify/actor-node-playwright-chrome:20
COPY package*.json ./
RUN npm --quiet set progress=false && npm install --include=dev
COPY . .
RUN npm run build
CMD npm start --silent
