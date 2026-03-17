FROM apify/actor-node:20
COPY package*.json ./
RUN npm --quiet set progress=false && npm install --include=dev
COPY . .
# shared/ is at ../../shared relative to this actor, but we pre-bundle with esbuild
# so dist/main.js already contains all shared code inlined — just run start
CMD npm start --silent
