import "server-only";
import { betterAuth } from "better-auth";
import { drizzleAdapter } from "better-auth/adapters/drizzle";
import { nextCookies } from "better-auth/next-js";
import { db } from "@/db";
import { sendVerificationEmail, sendResetPasswordEmail } from "@/lib/email";
import { type Locale, defaultLocale, isLocale } from "@/lib/i18n";

function localeFromRequest(request?: Request): Locale {
  const referer = request?.headers.get("referer") ?? "";
  const segment = new URL(referer, "http://localhost").pathname.split("/")[1];
  return segment && isLocale(segment) ? segment : defaultLocale;
}

export const auth = betterAuth({
  database: drizzleAdapter(db, { provider: "pg" }),
  emailAndPassword: {
    enabled: true,
    requireEmailVerification: true,
    revokeSessionsOnPasswordReset: true,
    sendResetPassword: async ({ user, url }, request) => {
      const locale = localeFromRequest(request);
      await sendResetPasswordEmail(user.email, url, locale);
    },
  },
  emailVerification: {
    sendOnSignUp: true,
    autoSignInAfterVerification: true,
    sendVerificationEmail: async ({ user, token }, request) => {
      const locale = localeFromRequest(request);
      const base = process.env.BETTER_AUTH_URL ?? "http://localhost:3000";
      const verifyUrl = `${base}/${locale}/verify-email?token=${token}`;
      void sendVerificationEmail(user.email, verifyUrl, locale);
    },
  },
  user: {
    changeEmail: {
      enabled: true,
    },
    deleteUser: {
      enabled: true,
    },
  },
  socialProviders: {
    github: {
      clientId: process.env.GITHUB_CLIENT_ID!,
      clientSecret: process.env.GITHUB_CLIENT_SECRET!,
    },
    google: {
      clientId: process.env.GOOGLE_CLIENT_ID!,
      clientSecret: process.env.GOOGLE_CLIENT_SECRET!,
    },
    linkedin: {
      clientId: process.env.LINKEDIN_CLIENT_ID!,
      clientSecret: process.env.LINKEDIN_CLIENT_SECRET!,
    },
  },
  plugins: [nextCookies()],
});
