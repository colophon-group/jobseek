import "server-only";

import { getSearchClient } from "@/lib/search/typesense-client";
import { assertTypesenseSearchResult } from "@/lib/search/typesense-retry";
import { AiFilterMiningPolicyError } from "./orchestrator";

/** Uncached, bounded check against the crawler's replicated reservation state. */
export async function assertAiFilterMiningAllowed(ids: readonly string[]): Promise<void> {
  if (ids.length === 0) return;
  if (ids.length > 50 || ids.some(id => !/^[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}$/.test(id))) {
    throw new AiFilterMiningPolicyError("tdm_policy_unavailable");
  }
  try {
    const result = await getSearchClient().collections("job_posting").documents().search({
      q: "*",
      query_by: "title",
      filter_by: `id:[${ids.join(",")}]`,
      include_fields: "id,tdm_reserved",
      per_page: ids.length,
    });
    assertTypesenseSearchResult(result);
    const found = new Set<string>();
    for (const hit of result.hits ?? []) {
      const doc = hit.document as { id?: unknown; tdm_reserved?: unknown };
      if (typeof doc.id !== "string" || !ids.includes(doc.id) ||
          (doc.tdm_reserved !== undefined && typeof doc.tdm_reserved !== "boolean")) {
        throw new Error("Invalid mining eligibility response");
      }
      if (doc.tdm_reserved === true) throw new AiFilterMiningPolicyError("tdm_reserved");
      found.add(doc.id);
    }
    if (found.size !== new Set(ids).size) throw new Error("Missing mining eligibility");
  } catch (error) {
    if (error instanceof AiFilterMiningPolicyError) throw error;
    throw new AiFilterMiningPolicyError("tdm_policy_unavailable");
  }
}
