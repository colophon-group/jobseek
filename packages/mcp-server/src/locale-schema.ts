import { z } from "zod";
import { API_LOCALES, DEFAULT_API_LOCALE } from "./public-api-contract.js";

/** The common locale input accepted by every public MCP tool. */
export const apiLocaleSchema = z
  .enum(API_LOCALES)
  .default(DEFAULT_API_LOCALE)
  .describe("Response language");
