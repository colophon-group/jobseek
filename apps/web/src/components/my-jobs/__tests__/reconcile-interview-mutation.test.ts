import { describe, expect, it, vi } from "vitest";
import type { InterviewEntry, MyJobDetail } from "@/lib/actions/my-jobs-types";
import { reconcileInterviewMutation } from "../reconcile-interview-mutation";

const firstInterview: InterviewEntry = {
  id: "interview-1",
  round: 1,
  type: "interview",
  scheduledAt: null,
  createdAt: "2026-07-22T00:00:00.000Z",
};

function detail(interviews: InterviewEntry[]): MyJobDetail {
  return {
    id: "saved-job-1",
    savedAt: "2026-07-22T00:00:00.000Z",
    status: "interviewing",
    statusChangedAt: "2026-07-22T00:00:00.000Z",
    appliedAt: "2026-07-22T00:00:00.000Z",
    offeredAt: null,
    rejectedAt: null,
    interviewCount: interviews.length,
    posting: {
      id: "posting-1",
      title: "Engineer",
      sourceUrl: "https://example.com/job",
      firstSeenAt: "2026-07-22T00:00:00.000Z",
      isActive: true,
      salaryMin: null,
      salaryMax: null,
      salaryCurrency: null,
      salaryPeriod: null,
    },
    company: {
      id: "company-1",
      name: "Example",
      slug: "example",
      icon: null,
    },
    salaryOverride: {
      min: null,
      max: null,
      currency: null,
      period: null,
    },
    interviews,
  };
}

describe("reconcileInterviewMutation", () => {
  it("recovers a committed mutation when the action response rejects", async () => {
    const persisted = {
      ...firstInterview,
      id: "interview-2",
      round: 2,
    };
    const applyDetail = vi.fn();

    const ok = await reconcileInterviewMutation({
      mutate: async () => {
        throw new Error("response lost after commit");
      },
      refresh: async () => detail([firstInterview, persisted]),
      applyDetail,
      verify: (current) => current.interviews.some((interview) => interview.id === persisted.id),
    });

    expect(ok).toBe(true);
    expect(applyDetail).toHaveBeenCalledWith(detail([firstInterview, persisted]));
  });

  it("reports failure when neither the action nor canonical state contains the change", async () => {
    const ok = await reconcileInterviewMutation({
      mutate: async () => ({ ok: false }),
      refresh: async () => detail([firstInterview]),
      applyDetail: vi.fn(),
      verify: (current) => current.interviews.length === 2,
    });

    expect(ok).toBe(false);
  });

  it("uses the successful payload fallback when canonical refresh is unavailable", async () => {
    const applyFallback = vi.fn(() => true);
    const result = { ok: true, interview: firstInterview };

    const ok = await reconcileInterviewMutation({
      mutate: async () => result,
      refresh: async () => {
        throw new Error("read unavailable");
      },
      applyDetail: vi.fn(),
      verify: () => false,
      applyFallback,
    });

    expect(ok).toBe(true);
    expect(applyFallback).toHaveBeenCalledWith(result);
  });
});
