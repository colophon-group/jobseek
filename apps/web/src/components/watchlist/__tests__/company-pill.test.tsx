import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import "@/test-utils/lingui-mock";

const route = vi.hoisted(() => ({ lang: "en" }));

vi.mock("next/navigation", () => ({ useParams: () => route }));
vi.mock("next/link", () => ({
  default: ({ children, prefetch: _prefetch, ...props }: React.AnchorHTMLAttributes<HTMLAnchorElement> & { prefetch?: boolean }) => (
    <a {...props}>{children}</a>
  ),
}));

import { CompanyPill } from "../company-pill";

const company = { id: "company-1", slug: "anybotics", name: "ANYbotics", icon: null };

afterEach(cleanup);

describe("CompanyPill", () => {
  it.each(["en", "de", "fr", "it"])("links shared pills to the %s company page", (lang) => {
    route.lang = lang;
    render(<CompanyPill company={company} />);
    expect(screen.getByRole("link", { name: company.name }).getAttribute("href"))
      .toBe(`/${lang}/company/anybotics`);
    expect(screen.queryByRole("button")).toBeNull();
  });

  it("keeps an unavailable selection removable without a fabricated company link", () => {
    const onRemove = vi.fn();
    render(<CompanyPill company={{ ...company, slug: "", unavailable: true }} onRemove={onRemove} />);
    expect(screen.queryByRole("link")).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Remove Company unavailable" }));
    expect(onRemove).toHaveBeenCalledWith(company.id);
  });

  it("keeps navigation and removal as independent owner controls", () => {
    const onRemove = vi.fn();
    const { container } = render(<CompanyPill company={company} onRemove={onRemove} />);
    const link = screen.getByRole("link", { name: company.name });
    const remove = screen.getByRole("button", { name: "Remove ANYbotics" });
    link.addEventListener("click", (event) => event.preventDefault());

    fireEvent.click(link);
    expect(onRemove).not.toHaveBeenCalled();
    expect(container.querySelector("a button, button a")).toBeNull();

    fireEvent.click(remove);
    expect(onRemove).toHaveBeenCalledExactlyOnceWith(company.id);
  });

  it("lets keyboard users focus the company link then activate its separate remove button", async () => {
    const user = userEvent.setup();
    const onRemove = vi.fn();
    render(<CompanyPill company={company} onRemove={onRemove} />);
    await user.tab();
    expect(document.activeElement).toBe(screen.getByRole("link", { name: company.name }));
    await user.tab();
    expect(document.activeElement).toBe(screen.getByRole("button", { name: "Remove ANYbotics" }));
    await user.keyboard("{Enter}");
    expect(onRemove).toHaveBeenCalledExactlyOnceWith(company.id);
  });
});
