FROM apify/actor-node:20
# libresolv.so.2 was merged into libc in glibc 2.34+ but node-tls-client's Go binary still links it
RUN ln -sf /lib/x86_64-linux-gnu/libc.so.6 /lib/x86_64-linux-gnu/libresolv.so.2 2>/dev/null || \
    ln -sf /lib/aarch64-linux-gnu/libc.so.6 /lib/aarch64-linux-gnu/libresolv.so.2 2>/dev/null || true
COPY package*.json ./
RUN npm --quiet set progress=false && npm install --include=dev
COPY . .
RUN npm run build
CMD npm start --silent
