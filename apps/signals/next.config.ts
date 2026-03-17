import type { NextConfig } from "next";
import path from "node:path";

const nextConfig: NextConfig = {
  output: "standalone",
  outputFileTracingRoot: path.join(__dirname, "../.."),
  serverExternalPackages: ["apify-client", "proxy-agent"],
  outputFileTracingIncludes: {
    "/api/apify/**": [
      "../../node_modules/.pnpm/proxy-agent@6.5.0/node_modules/**",
      "../../node_modules/.pnpm/pac-proxy-agent*/node_modules/**",
      "../../node_modules/.pnpm/http-proxy-agent*/node_modules/**",
      "../../node_modules/.pnpm/https-proxy-agent*/node_modules/**",
      "../../node_modules/.pnpm/socks-proxy-agent*/node_modules/**",
    ],
  },
};

export default nextConfig;
