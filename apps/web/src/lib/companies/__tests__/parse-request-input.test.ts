import { describe, it, expect } from "vitest";
import { parseRequestInput } from "../parse-request-input";

describe("parseRequestInput", () => {
  it("returns null for empty input", () => {
    expect(parseRequestInput("")).toBeNull();
    expect(parseRequestInput("   ")).toBeNull();
  });

  it("returns null for plain text (not a URL)", () => {
    expect(parseRequestInput("Stripe")).toBeNull();
    expect(parseRequestInput("acme.com")).toBeNull(); // no scheme
  });

  it("returns null for non-http(s) URLs", () => {
    // The leading-regex guard rejects these before URL parsing.
    expect(parseRequestInput("ftp://example.com")).toBeNull();
    expect(parseRequestInput("mailto:hi@example.com")).toBeNull();
  });

  it("derives hostname (without www) from an https URL", () => {
    expect(parseRequestInput("https://www.stripe.com/jobs")).toEqual({
      company_name: "stripe.com",
      website: "https://www.stripe.com/jobs",
    });
  });

  it("derives hostname from an http URL", () => {
    expect(parseRequestInput("http://acme.example")).toEqual({
      company_name: "acme.example",
      website: "http://acme.example",
    });
  });

  it("trims whitespace before parsing", () => {
    expect(parseRequestInput("  https://example.com  ")).toEqual({
      company_name: "example.com",
      website: "https://example.com",
    });
  });

  it("returns null for malformed URLs", () => {
    expect(parseRequestInput("https://")).toBeNull();
  });
});
