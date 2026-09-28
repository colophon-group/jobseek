import { beforeEach, describe, expect, it, vi } from "vitest";

const { send } = vi.hoisted(() => ({ send: vi.fn().mockResolvedValue({ data: { id: "message-1" }, error: null }) }));
vi.mock("server-only", () => ({}));
vi.mock("resend", () => ({
  Resend: class {
    emails = { send };
  },
}));

import { sendResetPasswordEmail, sendVerificationEmail } from "../email";

describe("account email sender", () => {
  beforeEach(() => send.mockClear());

  it.each([sendVerificationEmail, sendResetPasswordEmail])("uses the same replyable Jseek sender for %s", async sendEmail => {
    await sendEmail("a@example.com", "https://jseek.co/en/account?token=test", "en");
    expect(send).toHaveBeenCalledExactlyOnceWith(expect.objectContaining({
      from: "Job Seek <hello@jseek.co>",
      replyTo: "business@colophon-group.org",
      to: "a@example.com",
      html: expect.stringContaining("https://jseek.co/en/account?token=test"),
    }));
  });
});
