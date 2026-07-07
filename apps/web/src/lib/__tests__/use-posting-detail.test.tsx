import { act, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { getPostingDetail } from "@/lib/actions/search";
import type { PostingDetail } from "@/lib/actions/search";
import { usePostingDetail } from "@/lib/use-posting-detail";

vi.mock("@/lib/actions/search", () => ({
  getPostingDetail: vi.fn(),
}));

const getPostingDetailMock = vi.mocked(getPostingDetail);

function makePostingDetail(overrides: Partial<PostingDetail> = {}): PostingDetail {
  return {
    id: "posting-1",
    title: "Product Engineer",
    company: {
      id: "company-1",
      name: "Example",
      slug: "example",
      logo: null,
      icon: null,
    },
    locations: [],
    employmentType: null,
    experienceMin: null,
    experienceMax: null,
    technologies: [],
    salaryMin: null,
    salaryMax: null,
    salaryCurrency: null,
    salaryPeriod: null,
    seniority: null,
    sourceUrl: "https://example.com/jobs/posting-1",
    firstSeenAt: "2026-01-01T00:00:00.000Z",
    descriptionHtml: null,
    descriptionUrl: null,
    ...overrides,
  };
}

function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (reason?: unknown) => void;
  const promise = new Promise<T>((res, rej) => {
    resolve = res;
    reject = rej;
  });
  return { promise, resolve, reject };
}

function HookHarness({ postingId }: { postingId: string | null }) {
  const { detail, loading, error, descriptionLoaded } =
    usePostingDetail(postingId);

  return (
    <dl>
      <dt>loading</dt>
      <dd data-testid="loading">{String(loading)}</dd>
      <dt>error</dt>
      <dd data-testid="error">{String(error)}</dd>
      <dt>descriptionLoaded</dt>
      <dd data-testid="description-loaded">{String(descriptionLoaded)}</dd>
      <dt>title</dt>
      <dd data-testid="title">{detail?.title ?? ""}</dd>
      <dt>description</dt>
      <dd data-testid="description">{detail?.descriptionHtml ?? ""}</dd>
    </dl>
  );
}

function expectTestIdText(testId: string, text: string) {
  expect(screen.getByTestId(testId).textContent).toBe(text);
}

describe("usePostingDetail", () => {
  beforeEach(() => {
    vi.unstubAllGlobals();
    vi.clearAllMocks();
    document.documentElement.lang = "";
  });

  it("loads posting detail using the document locale and fetches deferred description HTML", async () => {
    document.documentElement.lang = "fr";
    getPostingDetailMock.mockResolvedValue(
      makePostingDetail({
        descriptionUrl: "https://r2.example/job/posting-1/fr/latest.html",
      }),
    );
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({
        ok: true,
        text: vi.fn().mockResolvedValue("<p>Bonjour</p>"),
      }),
    );

    render(<HookHarness postingId="posting-1" />);

    await waitFor(() =>
      expect(getPostingDetailMock).toHaveBeenCalledWith({
        postingId: "posting-1",
        locale: "fr",
      }),
    );
    await waitFor(() => expectTestIdText("description", "<p>Bonjour</p>"));

    expect(fetch).toHaveBeenCalledWith(
      "https://r2.example/job/posting-1/fr/latest.html",
    );
    expectTestIdText("loading", "false");
    expectTestIdText("error", "false");
    expectTestIdText("description-loaded", "true");
  });

  it("ignores stale posting-detail responses after the posting id changes", async () => {
    const oldRequest = deferred<PostingDetail | null>();
    getPostingDetailMock.mockImplementation(({ postingId }) => {
      if (postingId === "old-posting") return oldRequest.promise;
      return Promise.resolve(
        makePostingDetail({
          id: "new-posting",
          title: "New posting",
          descriptionHtml: "<p>Fresh</p>",
        }),
      );
    });

    const { rerender } = render(<HookHarness postingId="old-posting" />);

    rerender(<HookHarness postingId="new-posting" />);
    await act(async () => {
      oldRequest.resolve(
        makePostingDetail({
          id: "old-posting",
          title: "Old posting",
          descriptionHtml: "<p>Stale</p>",
        }),
      );
      await oldRequest.promise;
    });

    await waitFor(() => expectTestIdText("title", "New posting"));
    expectTestIdText("description", "<p>Fresh</p>");
    expect(screen.getByTestId("description").textContent).not.toContain(
      "Stale",
    );
  });

  it("marks the description as loaded when the deferred description fetch fails", async () => {
    getPostingDetailMock.mockResolvedValue(
      makePostingDetail({
        descriptionUrl: "https://r2.example/job/posting-1/en/latest.html",
      }),
    );
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new Error("R2 down")));

    render(<HookHarness postingId="posting-1" />);

    await waitFor(() => expectTestIdText("title", "Product Engineer"));
    await waitFor(() =>
      expectTestIdText("description-loaded", "true"),
    );

    expectTestIdText("loading", "false");
    expectTestIdText("error", "false");
    expectTestIdText("description", "");
  });

  it("resets without fetching when there is no posting id", () => {
    render(<HookHarness postingId={null} />);

    expect(getPostingDetailMock).not.toHaveBeenCalled();
    expectTestIdText("loading", "false");
    expectTestIdText("error", "false");
    expectTestIdText("description-loaded", "false");
  });
});
