import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

vi.mock("server-only", () => ({}));

// Stub the server action — useActionState will treat the returned shape as
// state. We return success so the success branch renders.
const mockRequestCompany = vi.fn();
vi.mock("@/lib/actions/stats", () => ({
  requestCompany: (_prev: unknown, fd: FormData) => mockRequestCompany(fd),
}));

// Stub the agent-run client helper.
const mockRequestAgentRun = vi.fn();
vi.mock("@/lib/companies/request-agent-run", () => ({
  requestAgentRun: (input: unknown) => mockRequestAgentRun(input),
}));

// Stub the success card so we can assert it rendered with the right inputs.
vi.mock("@/components/search/request-company-success", () => ({
  RequestCompanySuccess: (props: {
    companyName: string;
    agentRun: unknown;
    serverActionState: { issueNumber?: number; issueCreationFailed?: boolean };
  }) => (
    <div
      data-testid="success-stub"
      data-company-name={props.companyName}
      data-agent-run-kind={
        (props.agentRun as { kind?: string } | null)?.kind ?? ""
      }
    />
  ),
}));

// Lingui macros — stub so the component can be rendered outside a real
// i18n provider. Shared across every Lingui-aware test (#2814).
import "@/test-utils/lingui-mock";

import { CompanyRequestPageForm } from "../company-request-page-form";

beforeEach(() => {
  mockRequestCompany.mockReset();
  mockRequestAgentRun.mockReset();
});

afterEach(() => {
  vi.clearAllMocks();
});

describe("CompanyRequestPageForm", () => {
  it("renders the input with `defaultName` when no website is provided", () => {
    render(<CompanyRequestPageForm locale="en" defaultName="Anthropic" />);

    const input = screen.getByRole("textbox") as HTMLInputElement;
    expect(input.value).toBe("Anthropic");
  });

  it("renders the input prefilled with the website URL when both name + website are provided", () => {
    render(
      <CompanyRequestPageForm
        locale="en"
        defaultName="Anthropic"
        defaultWebsite="https://anthropic.com"
      />,
    );

    const input = screen.getByRole("textbox") as HTMLInputElement;
    // The URL gives the agent-run path the website it needs; the legacy
    // single-input form is URL-aware via parseRequestInput.
    expect(input.value).toBe("https://anthropic.com");
  });

  it("renders an empty input when no defaults are provided", () => {
    render(<CompanyRequestPageForm locale="en" />);
    const input = screen.getByRole("textbox") as HTMLInputElement;
    expect(input.value).toBe("");
  });

  it("renders a hidden `locale` field set from props", () => {
    const { container } = render(<CompanyRequestPageForm locale="de" />);
    const hidden = container.querySelector(
      "input[type='hidden'][name='locale']",
    ) as HTMLInputElement | null;
    expect(hidden).not.toBeNull();
    expect(hidden?.value).toBe("de");
  });

  it("submits the form, fires the server action AND the agent-run call when input is a URL, then renders the AgentPromptCard branch", async () => {
    const user = userEvent.setup();
    mockRequestCompany.mockResolvedValue({ success: true, issueNumber: 42 });
    mockRequestAgentRun.mockResolvedValue({
      kind: "ok",
      runId: "run_abc",
      agentPrompt: "Add anthropic.com to jobseek...",
    });

    render(
      <CompanyRequestPageForm
        locale="en"
        defaultName="Anthropic"
        defaultWebsite="https://anthropic.com"
      />,
    );

    const submit = screen.getByRole("button");
    await user.click(submit);

    await waitFor(() => {
      expect(mockRequestCompany).toHaveBeenCalledTimes(1);
    });
    await waitFor(() => {
      expect(mockRequestAgentRun).toHaveBeenCalledTimes(1);
    });
    const arg = mockRequestAgentRun.mock.calls[0]?.[0] as {
      companyName: string;
      website: string;
    };
    expect(arg.website).toBe("https://anthropic.com");
    expect(arg.companyName).toBe("anthropic.com");

    await waitFor(() => {
      expect(screen.getByTestId("success-stub")).toBeTruthy();
    });
    const stub = screen.getByTestId("success-stub");
    expect(stub.getAttribute("data-agent-run-kind")).toBe("ok");
  });

  it("does NOT fire requestAgentRun when the input is not a URL (legacy GH-issue path)", async () => {
    const user = userEvent.setup();
    mockRequestCompany.mockResolvedValue({ success: true });

    render(<CompanyRequestPageForm locale="en" defaultName="Anthropic" />);

    const submit = screen.getByRole("button");
    await user.click(submit);

    await waitFor(() => {
      expect(mockRequestCompany).toHaveBeenCalledTimes(1);
    });
    expect(mockRequestAgentRun).not.toHaveBeenCalled();
  });
});
