import "server-only";
import Stripe from "stripe";
import { stripeApiKey } from "./config";

export function getStripe() {
  return new Stripe(stripeApiKey(), { apiVersion: "2026-08-26.dahlia", maxNetworkRetries: 2, timeout: 20_000 });
}
