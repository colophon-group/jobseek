import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";

// Mock lucide-react
vi.mock("lucide-react", () => ({
  Trash2: ({ className }: { className: string }) => <div data-testid="trash-icon" className={className} />,
}));

vi.mock("@/lib/actions/customization-history", () => ({
  getCustomizationHistory: vi.fn(),
  getCustomizationCount: vi.fn(),
  deleteCustomization: vi.fn(),
}));

import { CustomizationHistoryPanel } from "../CustomizationHistoryPanel";
import * as historyActions from "@/lib/actions/customization-history";

const mocks = {
  getCustomizationHistory: vi.mocked(historyActions.getCustomizationHistory),
  getCustomizationCount: vi.mocked(historyActions.getCustomizationCount),
  deleteCustomization: vi.mocked(historyActions.deleteCustomization),
};

describe("CustomizationHistoryPanel", () => {
  const mockItems = [
    {
      id: "1",
      queueId: "q1",
      postingId: "p1",
      jobTitle: "Senior Engineer",
      insertedKeywords: ["React", "TypeScript", "Node.js"],
      createdAt: new Date(Date.now() - 86400000),
    },
    {
      id: "2",
      queueId: "q2",
      postingId: "p2",
      jobTitle: "Tech Lead",
      insertedKeywords: ["Kubernetes", "Microservices", "AWS", "Docker", "Terraform"],
      createdAt: new Date(Date.now() - 3600000),
    },
  ];

  beforeEach(() => {
    mocks.getCustomizationHistory.mockClear();
    mocks.getCustomizationCount.mockClear();
    mocks.deleteCustomization.mockClear();
  });

  it("should display loading spinner on initial load", async () => {
    mocks.getCustomizationHistory.mockImplementation(
      () =>
        new Promise((resolve) => {
          setTimeout(() => resolve(mockItems), 100);
        }),
    );
    mocks.getCustomizationCount.mockResolvedValue(2);

    render(<CustomizationHistoryPanel />);

    const spinner = document.querySelector(".animate-spin");
    expect(spinner).toBeTruthy();

    await waitFor(() => {
      expect(screen.getByText("Senior Engineer")).toBeTruthy();
    });
  });

  it("should display empty state when no customizations exist", async () => {
    mocks.getCustomizationHistory.mockResolvedValue([]);
    mocks.getCustomizationCount.mockResolvedValue(0);

    render(<CustomizationHistoryPanel />);

    await waitFor(() => {
      expect(screen.getByText("No customizations yet")).toBeTruthy();
    });
  });

  it("should display customization items with job titles and keywords", async () => {
    mocks.getCustomizationHistory.mockResolvedValue(mockItems);
    mocks.getCustomizationCount.mockResolvedValue(2);

    render(<CustomizationHistoryPanel />);

    await waitFor(() => {
      expect(screen.getByText("Senior Engineer")).toBeTruthy();
      expect(screen.getByText("Tech Lead")).toBeTruthy();
      expect(screen.getByText("React")).toBeTruthy();
      expect(screen.getByText("Kubernetes")).toBeTruthy();
    });
  });

  it("should show +N more for keywords exceeding 3", async () => {
    mocks.getCustomizationHistory.mockResolvedValue(mockItems);
    mocks.getCustomizationCount.mockResolvedValue(2);

    render(<CustomizationHistoryPanel />);

    await waitFor(() => {
      expect(screen.getByText("+2 more")).toBeTruthy();
    });
  });

  it("should display total customization count", async () => {
    mocks.getCustomizationHistory.mockResolvedValue(mockItems);
    mocks.getCustomizationCount.mockResolvedValue(42);

    render(<CustomizationHistoryPanel />);

    await waitFor(() => {
      expect(screen.getByText("42 total")).toBeTruthy();
    });
  });

  it("should display pagination controls when items exceed limit", async () => {
    const manyItems = Array.from({ length: 10 }, (_, i) => ({
      ...mockItems[0],
      id: `${i}`,
      jobTitle: `Job ${i}`,
    }));

    mocks.getCustomizationHistory.mockResolvedValue(manyItems);
    mocks.getCustomizationCount.mockResolvedValue(25);

    render(<CustomizationHistoryPanel />);

    await waitFor(() => {
      expect(screen.getByText("Next")).toBeTruthy();
      expect(screen.getByText("1-10 of 25")).toBeTruthy();
    });
  });

  it("should disable previous button on first page", async () => {
    mocks.getCustomizationHistory.mockResolvedValue(mockItems);
    mocks.getCustomizationCount.mockResolvedValue(15);

    render(<CustomizationHistoryPanel />);

    await waitFor(() => {
      const previousButton = screen.getByText("Previous");
      expect(previousButton.getAttribute("disabled")).not.toBeNull();
    });
  });

  it("should handle delete action and remove item from list", async () => {
    mocks.getCustomizationHistory.mockResolvedValue(mockItems);
    mocks.getCustomizationCount.mockResolvedValue(2);
    mocks.deleteCustomization.mockResolvedValue({ deleted: true });

    render(<CustomizationHistoryPanel />);

    await waitFor(() => {
      expect(screen.getByText("Senior Engineer")).toBeTruthy();
    });

    const deleteButtons = screen.getAllByRole("button", { name: /Delete customization/ });
    fireEvent.click(deleteButtons[0]);

    await waitFor(() => {
      expect(mocks.deleteCustomization).toHaveBeenCalledWith("1");
    });
  });

  it("should handle pagination by updating offset", async () => {
    mocks.getCustomizationHistory.mockResolvedValue(mockItems);
    mocks.getCustomizationCount.mockResolvedValue(25);

    render(<CustomizationHistoryPanel />);

    await waitFor(() => {
      expect(screen.getByText("Next")).toBeTruthy();
    });

    const nextButton = screen.getByText("Next");
    fireEvent.click(nextButton);

    await waitFor(() => {
      expect(mocks.getCustomizationHistory).toHaveBeenCalledWith({
        limit: 10,
        offset: 10,
      });
    });
  });

  it("should handle load errors gracefully", async () => {
    mocks.getCustomizationHistory.mockRejectedValue(new Error("Load failed"));
    mocks.getCustomizationCount.mockRejectedValue(new Error("Count failed"));

    render(<CustomizationHistoryPanel />);

    await waitFor(() => {
      expect(screen.getByText("No customizations yet")).toBeTruthy();
    });
  });

  it("should format dates as relative time", async () => {
    const oneHourAgo = new Date(Date.now() - 3600000);
    const itemsWithTime = [
      {
        ...mockItems[0],
        createdAt: oneHourAgo,
      },
    ];

    mocks.getCustomizationHistory.mockResolvedValue(itemsWithTime);
    mocks.getCustomizationCount.mockResolvedValue(1);

    render(<CustomizationHistoryPanel />);

    await waitFor(() => {
      const timeElement = screen.getByText(/h ago/);
      expect(timeElement).toBeTruthy();
    });
  });
});
