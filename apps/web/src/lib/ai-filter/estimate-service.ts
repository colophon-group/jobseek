import "server-only";

import { countAiFilterCandidates } from "./candidate-loader";
import { getAiFilterEstimateContext } from "./configuration-service";
import {
  AI_FILTER_USER_MONTHLY_BUDGET_NANODOLLARS,
  JEV_COMPACT_DECISION_ESTIMATE_NANODOLLARS,
  JEV_MAX_DESCRIPTION_DECISION_ESTIMATE_NANODOLLARS,
} from "./policy";

export async function getAiFilterEstimate(input: {
  ownerId: string;
  watchlistId: string;
  now?: Date;
  signal?: AbortSignal;
}) {
  const context = await getAiFilterEstimateContext(input);
  // The estimate is informative for subscribers. Free accounts already see
  // the ordinary search count and should not be able to drive an extra
  // stable-order Typesense query through this endpoint.
  const candidateCount = context.entitled
    ? await countAiFilterCandidates(input)
    : null;
  return Object.freeze({
    candidateCount,
    estimatedNanodollars: candidateCount === null
      ? null
      : Object.freeze({
          compact: candidateCount * JEV_COMPACT_DECISION_ESTIMATE_NANODOLLARS,
          maximumDescription:
            candidateCount * JEV_MAX_DESCRIPTION_DECISION_ESTIMATE_NANODOLLARS,
        }),
    monthlyBudgetNanodollars: AI_FILTER_USER_MONTHLY_BUDGET_NANODOLLARS,
    actualNanodollars: context.actualNanodollars,
    reservedNanodollars: context.reservedNanodollars,
    entitled: context.entitled,
  });
}
