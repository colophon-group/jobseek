import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import "@/test-utils/lingui-mock";

const action = vi.hoisted(() => ({ toggle: vi.fn() }));
vi.mock("@/lib/actions/starred-companies", () => ({ toggleStarredCompany: action.toggle }));
vi.mock("@/components/providers/SessionProvider", () => ({ useSession: () => ({ isLoggedIn: true, isPending: false }) }));
vi.mock("@/lib/useLocalePath", () => ({ useLocalePath: () => (path: string) => `/en${path}` }));
import { StarredCompaniesProvider } from "@/components/providers/StarredCompaniesProvider";
import { StarButton } from "../star-button";

afterEach(() => { cleanup(); vi.clearAllMocks(); });
describe("StarButton persistence", () => {
  it("rolls back a typed lookup failure and exposes an accessible retry explanation", async () => {
    action.toggle.mockResolvedValue({ error: "company_lookup_unavailable" });
    render(<StarredCompaniesProvider><StarButton companyId="company-1" /></StarredCompaniesProvider>);
    fireEvent.click(screen.getByRole("button", { name: "Star" }));
    expect((await screen.findByRole("alert")).textContent).toBe(
      "Company lookup is temporarily unavailable. Please try again.",
    );
    expect(screen.getByRole("button", { name: "Star" }).hasAttribute("disabled")).toBe(false);
    action.toggle.mockResolvedValue({ starred: true });
    fireEvent.click(screen.getByRole("button", { name: "Star" }));
    await waitFor(() => expect(screen.getByRole("button", { name: "Starred" })).toBeTruthy());
    expect(screen.queryByRole("alert")).toBeNull();
  });
  it("restores an existing star when unstar fails", async () => {
    action.toggle.mockRejectedValue(new Error("private backend detail"));
    render(<StarredCompaniesProvider initialIds={["company-1"]}><StarButton companyId="company-1" /></StarredCompaniesProvider>);
    fireEvent.click(screen.getByRole("button", { name: "Starred" }));
    await screen.findByRole("alert");
    expect(screen.getByRole("button", { name: "Starred" })).toBeTruthy();
    expect(screen.queryByText("private backend detail")).toBeNull();
  });
});
