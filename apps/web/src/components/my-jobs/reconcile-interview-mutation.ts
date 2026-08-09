import type { MyJobDetail } from "@/lib/actions/my-jobs-types";

type MutationResult = { ok: boolean };

/**
 * Server actions can commit before their response reaches the client. Always
 * read back the canonical tracker after a mutation so a committed row does not
 * remain invisible when the action response rejects or is otherwise lost.
 */
export async function reconcileInterviewMutation<T extends MutationResult>({
  mutate,
  refresh,
  applyDetail,
  verify,
  applyFallback,
}: {
  mutate: () => Promise<T>;
  refresh: () => Promise<MyJobDetail | null>;
  applyDetail: (detail: MyJobDetail) => void;
  verify: (detail: MyJobDetail) => boolean;
  applyFallback?: (result: T) => boolean;
}): Promise<boolean> {
  let result: T | undefined;

  try {
    result = await mutate();
  } catch {
    // A write may already have committed. The canonical read below decides.
  }

  try {
    const detail = await refresh();
    if (detail) {
      applyDetail(detail);
      return verify(detail);
    }
  } catch {
    // Fall back to the successful action payload when the read-back fails.
  }

  if (!result?.ok) return false;
  return applyFallback ? applyFallback(result) : true;
}
