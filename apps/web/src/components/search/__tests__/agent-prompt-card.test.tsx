import { afterEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { AgentPromptCard, type AgentPromptCardProps } from "../agent-prompt-card";

const labels: AgentPromptCardProps["labels"] = {
  headingPrefix: "We're working on adding",
  body: "You can speed this up by asking your AI agent to complete it via Murmur.",
  copyButton: "Copy prompt",
  copied: "Copied",
  copyFailed: "Copy failed",
  runIdLabel: "Run id",
  promptRegionLabel: "Agent prompt",
};

afterEach(() => {
  vi.useRealTimers();
});

describe("AgentPromptCard", () => {
  it("renders the heading with the verbatim company name", () => {
    render(
      <AgentPromptCard
        companyName="Stripe, Inc."
        runId="run_xyz"
        agentPrompt="Add Stripe to jobseek..."
        labels={labels}
      />,
    );
    const heading = screen.getByRole("heading", { level: 3 });
    expect(heading.textContent).toBe("We're working on adding Stripe, Inc.");
  });

  it("renders the body copy", () => {
    render(
      <AgentPromptCard
        companyName="Acme"
        runId="run_xyz"
        agentPrompt="prompt"
        labels={labels}
      />,
    );
    expect(
      screen.getByText(
        /You can speed this up by asking your AI agent to complete it via Murmur\./,
      ),
    ).toBeTruthy();
  });

  it("renders the agent_prompt verbatim inside a labelled region", () => {
    const longPrompt =
      "Add Acme (https://acme.example) to jobseek. The Murmur run id is run_abc. Use pull_task...";
    render(
      <AgentPromptCard
        companyName="Acme"
        runId="run_abc"
        agentPrompt={longPrompt}
        labels={labels}
      />,
    );
    const region = screen.getByRole("region", { name: "Agent prompt" });
    expect(region).toBeTruthy();
    expect(region.textContent).toContain(longPrompt);
  });

  it("renders the run_id inside a select-all element so the user can copy it", () => {
    render(
      <AgentPromptCard
        companyName="Acme"
        runId="run_select_me"
        agentPrompt="prompt"
        labels={labels}
      />,
    );
    const runIdEl = screen.getByTestId("agent-prompt-card-run-id");
    expect(runIdEl.textContent).toBe("run_select_me");
    expect(runIdEl.className).toContain("select-all");
  });

  it("writes the prompt to the clipboard and shows the 'Copied' toast", async () => {
    const user = userEvent.setup();
    const writeToClipboard = vi.fn().mockResolvedValue(undefined);

    render(
      <AgentPromptCard
        companyName="Acme"
        runId="run_xyz"
        agentPrompt="THE PROMPT"
        labels={labels}
        writeToClipboard={writeToClipboard}
      />,
    );

    const button = screen.getByRole("button", { name: "Copy prompt" });
    await user.click(button);

    expect(writeToClipboard).toHaveBeenCalledWith("THE PROMPT");
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
        agentPrompt="THE PROMPT"
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

  it("copy button is keyboard reachable (focusable + Enter triggers copy)", async () => {
    const user = userEvent.setup();
    const writeToClipboard = vi.fn().mockResolvedValue(undefined);

    render(
      <AgentPromptCard
        companyName="Acme"
        runId="run_xyz"
        agentPrompt="THE PROMPT"
        labels={labels}
        writeToClipboard={writeToClipboard}
      />,
    );

    const button = screen.getByRole("button", { name: "Copy prompt" });
    await user.tab();
    expect(document.activeElement).toBe(button);
    await user.keyboard("{Enter}");
    expect(writeToClipboard).toHaveBeenCalledTimes(1);
  });
});
