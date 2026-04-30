import { afterEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { AgentPromptCard, type AgentPromptCardProps } from "../agent-prompt-card";

const labels: AgentPromptCardProps["labels"] = {
  headingPrefix: "We're working on adding",
  body: "You can speed this up by asking your AI agent to complete it via Murmur.",
  installHeading: "1. Install MCP",
  runHeading: "2. Run the prompt",
  tokenCaveat:
    "You'll need a token from the jobseek team — replace <token-from-jobseek-team> before running the install command.",
  copyInstallButton: "Copy command",
  copyPromptButton: "Copy prompt",
  copied: "Copied",
  copyFailed: "Copy failed",
  runIdLabel: "Run id",
  installRegionLabel: "MCP install command",
  promptRegionLabel: "Agent prompt",
};

const SAMPLE_INSTALL =
  'claude mcp add --transport http --scope user murmur https://murmur.colophon-group.org/mcp --header "Authorization: Bearer <token-from-jobseek-team>"';
const SAMPLE_PROMPT =
  "Add Acme (https://acme.example) to jobseek. The Murmur run id is run_abc. Call pull_task...";

afterEach(() => {
  vi.useRealTimers();
});

describe("AgentPromptCard", () => {
  it("renders the heading with the verbatim company name", () => {
    render(
      <AgentPromptCard
        companyName="Stripe, Inc."
        runId="run_xyz"
        installCommand={SAMPLE_INSTALL}
        promptText={SAMPLE_PROMPT}
        labels={labels}
      />,
    );
    const heading = screen.getByRole("heading", { level: 3 });
    expect(heading.textContent).toBe("We're working on adding Stripe, Inc.");
  });

  // Regression test for the PR #2806 review blocker: the heading must show the
  // derived display name (e.g. "stripe.com") that callers compute via
  // `parseRequestInput`, NOT the raw URL the user typed
  // (e.g. "https://www.stripe.com/jobs"). This pins the card-side end of the
  // contract; see also parse-request-input.test.ts for the caller-side end.
  it("renders the heading with a derived display name (caller passes company_name, not raw URL)", () => {
    render(
      <AgentPromptCard
        companyName="stripe.com"
        runId="run_xyz"
        installCommand={SAMPLE_INSTALL}
        promptText={SAMPLE_PROMPT}
        labels={labels}
      />,
    );
    const heading = screen.getByRole("heading", { level: 3 });
    expect(heading.textContent).toBe("We're working on adding stripe.com");
    expect(heading.textContent).not.toContain("https://");
    expect(heading.textContent).not.toContain("/jobs");
  });

  it("renders the body copy", () => {
    render(
      <AgentPromptCard
        companyName="Acme"
        runId="run_xyz"
        installCommand={SAMPLE_INSTALL}
        promptText={SAMPLE_PROMPT}
        labels={labels}
      />,
    );
    expect(
      screen.getByText(
        /You can speed this up by asking your AI agent to complete it via Murmur\./,
      ),
    ).toBeTruthy();
  });

  it("renders BOTH the install and run sections, each in its own labelled region", () => {
    render(
      <AgentPromptCard
        companyName="Acme"
        runId="run_abc"
        installCommand={SAMPLE_INSTALL}
        promptText={SAMPLE_PROMPT}
        labels={labels}
      />,
    );

    // Section headings.
    expect(screen.getByText("1. Install MCP")).toBeTruthy();
    expect(screen.getByText("2. Run the prompt")).toBeTruthy();

    // Install region carries the install_command verbatim and NOT the prompt.
    const installRegion = screen.getByRole("region", {
      name: "MCP install command",
    });
    expect(installRegion.textContent).toContain(SAMPLE_INSTALL);
    expect(installRegion.textContent).not.toContain(SAMPLE_PROMPT);

    // Prompt region carries the prompt_text verbatim and NOT the install line.
    const promptRegion = screen.getByRole("region", { name: "Agent prompt" });
    expect(promptRegion.textContent).toContain(SAMPLE_PROMPT);
    expect(promptRegion.textContent).not.toContain(SAMPLE_INSTALL);
  });

  it("renders the token caveat referencing the literal placeholder", () => {
    render(
      <AgentPromptCard
        companyName="Acme"
        runId="run_abc"
        installCommand={SAMPLE_INSTALL}
        promptText={SAMPLE_PROMPT}
        labels={labels}
      />,
    );
    // The literal placeholder appears in BOTH the install command (the
    // verbatim shell line) and the caveat sentence. Match the caveat by its
    // full sentence so we know we found the user-facing prose, not just the
    // shell echo.
    expect(
      screen.getByText(
        /You'll need a token from the jobseek team .* replace <token-from-jobseek-team> before running the install command\./,
      ),
    ).toBeTruthy();
  });

  it("renders the run_id inside a select-all element so the user can copy it", () => {
    render(
      <AgentPromptCard
        companyName="Acme"
        runId="run_select_me"
        installCommand={SAMPLE_INSTALL}
        promptText={SAMPLE_PROMPT}
        labels={labels}
      />,
    );
    const runIdEl = screen.getByTestId("agent-prompt-card-run-id");
    expect(runIdEl.textContent).toBe("run_select_me");
    expect(runIdEl.className).toContain("select-all");
  });

  it("install copy button writes ONLY installCommand to the clipboard (not promptText)", async () => {
    const user = userEvent.setup();
    const writeToClipboard = vi.fn().mockResolvedValue(undefined);

    render(
      <AgentPromptCard
        companyName="Acme"
        runId="run_xyz"
        installCommand={SAMPLE_INSTALL}
        promptText={SAMPLE_PROMPT}
        labels={labels}
        writeToClipboard={writeToClipboard}
      />,
    );

    await user.click(screen.getByRole("button", { name: "Copy command" }));

    expect(writeToClipboard).toHaveBeenCalledTimes(1);
    expect(writeToClipboard).toHaveBeenCalledWith(SAMPLE_INSTALL);
    // The prompt text must NOT have been written.
    const written = writeToClipboard.mock.calls[0]?.[0] ?? "";
    expect(written).not.toContain(SAMPLE_PROMPT);
  });

  it("prompt copy button writes ONLY promptText to the clipboard (not installCommand)", async () => {
    const user = userEvent.setup();
    const writeToClipboard = vi.fn().mockResolvedValue(undefined);

    render(
      <AgentPromptCard
        companyName="Acme"
        runId="run_xyz"
        installCommand={SAMPLE_INSTALL}
        promptText={SAMPLE_PROMPT}
        labels={labels}
        writeToClipboard={writeToClipboard}
      />,
    );

    await user.click(screen.getByRole("button", { name: "Copy prompt" }));

    expect(writeToClipboard).toHaveBeenCalledTimes(1);
    expect(writeToClipboard).toHaveBeenCalledWith(SAMPLE_PROMPT);
    const written = writeToClipboard.mock.calls[0]?.[0] ?? "";
    expect(written).not.toContain(SAMPLE_INSTALL);
  });

  it("shows the 'Copied' toast after a successful copy from either button", async () => {
    const user = userEvent.setup();
    const writeToClipboard = vi.fn().mockResolvedValue(undefined);

    render(
      <AgentPromptCard
        companyName="Acme"
        runId="run_xyz"
        installCommand={SAMPLE_INSTALL}
        promptText={SAMPLE_PROMPT}
        labels={labels}
        writeToClipboard={writeToClipboard}
      />,
    );

    await user.click(screen.getByRole("button", { name: "Copy command" }));
    await waitFor(() => {
      expect(screen.getByTestId("agent-prompt-card-toast").textContent).toBe(
        "Copied",
      );
    });
  });

  it("shows the failure toast when clipboard write rejects", async () => {
    const user = userEvent.setup();
    const writeToClipboard = vi
      .fn()
      .mockRejectedValue(new Error("clipboard blocked"));

    render(
      <AgentPromptCard
        companyName="Acme"
        runId="run_xyz"
        installCommand={SAMPLE_INSTALL}
        promptText={SAMPLE_PROMPT}
        labels={labels}
        writeToClipboard={writeToClipboard}
      />,
    );

    await user.click(screen.getByRole("button", { name: "Copy prompt" }));

    await waitFor(() => {
      expect(screen.getByTestId("agent-prompt-card-toast").textContent).toBe(
        "Copy failed",
      );
    });
  });

  it("both copy buttons are keyboard reachable (Tab + Enter triggers copy)", async () => {
    const user = userEvent.setup();
    const writeToClipboard = vi.fn().mockResolvedValue(undefined);

    render(
      <AgentPromptCard
        companyName="Acme"
        runId="run_xyz"
        installCommand={SAMPLE_INSTALL}
        promptText={SAMPLE_PROMPT}
        labels={labels}
        writeToClipboard={writeToClipboard}
      />,
    );

    const installButton = screen.getByRole("button", { name: "Copy command" });
    const promptButton = screen.getByRole("button", { name: "Copy prompt" });

    await user.tab();
    expect(document.activeElement).toBe(installButton);
    await user.keyboard("{Enter}");
    expect(writeToClipboard).toHaveBeenLastCalledWith(SAMPLE_INSTALL);

    await user.tab();
    expect(document.activeElement).toBe(promptButton);
    await user.keyboard("{Enter}");
    expect(writeToClipboard).toHaveBeenLastCalledWith(SAMPLE_PROMPT);
  });
});
