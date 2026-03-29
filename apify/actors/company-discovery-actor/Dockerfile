FROM apify/actor-node-playwright-chrome:20
COPY package*.json ./
RUN npm --quiet set progress=false && npm install --include=dev
COPY . .
RUN npm run build
CMD npm start --silent
