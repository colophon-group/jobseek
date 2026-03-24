import "server-only";
import { betterAuth } from "better-auth";
import { createAuthMiddleware } from "better-auth/api";
import { username } from "better-auth/plugins";
import { drizzleAdapter } from "better-auth/adapters/drizzle";
import { nextCookies } from "better-auth/next-js";
import { db } from "@/db";
import { sendVerificationEmail, sendResetPasswordEmail } from "@/lib/email";
import { type Locale, defaultLocale, isLocale } from "@/lib/i18n";
import { invalidateSessionCache } from "@/lib/sessionCache";
import { sql } from "drizzle-orm";
import { usernameFromEmail, withRandomSuffix, isReservedUsername } from "@/lib/username";

function localeFromRequest(request?: Request): Locale {
  const referer = request?.headers.get("referer") ?? "";
  const segment = new URL(referer, "http://localhost").pathname.split("/")[1];
  return segment && isLocale(segment) ? segment : defaultLocale;
}

export const auth = betterAuth({
  trustedOrigins: (process.env.TRUSTED_ORIGINS ?? "").split(",").filter(Boolean),
  database: drizzleAdapter(db, { provider: "pg" }),
  emailAndPassword: {
    enabled: true,
    requireEmailVerification: false,
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
      await sendVerificationEmail(user.email, verifyUrl, locale);
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
  hooks: {
    after: createAuthMiddleware(async (ctx) => {
      if (
        ctx.path.startsWith("/sign-out") ||
        ctx.path.startsWith("/revoke-session") ||
        ctx.path.startsWith("/revoke-sessions") ||
        ctx.path.startsWith("/reset-password")
      ) {
        const cookie = ctx.headers?.get("cookie") ?? "";
        for (const part of cookie.split(";")) {
          const trimmed = part.trim();
          const prefix = trimmed.startsWith(
            "__Secure-better-auth.session_token=",
          )
            ? "__Secure-better-auth.session_token="
            : trimmed.startsWith("better-auth.session_token=")
              ? "better-auth.session_token="
              : null;
          if (prefix) {
            await invalidateSessionCache(trimmed.slice(prefix.length));
            break;
          }
        }
      }
    }),
  },
  databaseHooks: {
    user: {
      create: {
        before: async (user) => {
          if (!user.email) return { data: user };

          const provided = (user as Record<string, unknown>).username as string | undefined;
          let candidate = provided || usernameFromEmail(user.email);

          // Always verify uniqueness + blocklist, suffix if needed
          const base = usernameFromEmail(user.email);
          for (let i = 0; i < 5; i++) {
            if (isReservedUsername(candidate)) {
              candidate = withRandomSuffix(base);
              continue;
            }
            const rows = await db.execute(
              sql`SELECT 1 FROM "user" WHERE username = ${candidate} LIMIT 1`,
            );
            if (!rows.length) break;
            candidate = withRandomSuffix(base);
          }

          return { data: { ...user, username: candidate, displayUsername: candidate } };
        },
      },
    },
  },
  plugins: [
    username({
      minUsernameLength: 3,
      maxUsernameLength: 30,
      usernameValidator: (username) => /^[a-z0-9][a-z0-9-]*[a-z0-9]$/.test(username),
    }),
    nextCookies(),
  ],
});
