#!/usr/bin/env node
import { StdioServerTransport } from "@modelcontextprotocol/sdk/server/stdio.js";
import { createServer } from "./server.js";

const args = process.argv.slice(2);
const baseUrlIdx = args.indexOf("--base-url");
const baseUrl =
  baseUrlIdx !== -1 && args[baseUrlIdx + 1]
    ? args[baseUrlIdx + 1]
    : "https://jseek.co";

const server = createServer(baseUrl);
const transport = new StdioServerTransport();
await server.connect(transport);
