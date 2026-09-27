import "server-only";

import { Environment, LogLevel, Paddle } from "@paddle/paddle-node-sdk";
import { paddleApiKey, paddleEnvironment } from "./config";

export function getPaddle() {
  return new Paddle(paddleApiKey(), {
    environment: paddleEnvironment() === "sandbox" ? Environment.sandbox : Environment.production,
    logLevel: LogLevel.none,
  });
}
