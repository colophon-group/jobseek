import { createHmac } from "node:crypto";

export interface ScopedKeyEmbed {
  filter_by?: string;
  exclude_fields?: string;
  use_cache?: boolean;
}

export function generateScopedSearchKey(
  parentKey: string,
  embed: ScopedKeyEmbed,
): string {
  const paramsJSON = JSON.stringify(embed);
  const digest = createHmac("sha256", parentKey)
    .update(paramsJSON)
    .digest("base64");
  const keyPrefix = parentKey.slice(0, 4);
  return Buffer.from(`${digest}${keyPrefix}${paramsJSON}`).toString("base64");
}
